"""
GBDT Signal Generator
Uses the trained multi-timeframe GBDT ensemble model from 'model & backtest result'.
Features: 48 multi-timeframe technical indicators (6 timeframes × 8 features each)
Models: LightGBM + XGBoost + CatBoost ensemble with calibration
"""

import os
import joblib
import numpy as np
import pandas as pd
import warnings
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

warnings.filterwarnings('ignore', message='.*feature names.*')
warnings.filterwarnings('ignore', category=UserWarning)

# Paths — model lives alongside this file in backend/ml/models/
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models')
DEFAULT_MODEL_BASENAME = 'EURUSD_gbdt_experimental.pkl'
LATEST_MODEL_PATH = os.path.join(MODELS_DIR, DEFAULT_MODEL_BASENAME)


def resolve_model_path() -> str:
    """Return the fixed production model path.

    Runtime policy: use only EURUSD_gbdt_experimental.pkl.
    """
    return LATEST_MODEL_PATH


# ==================== Feature Engineering (matches build_from_train.py) ====================

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI indicator"""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range"""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr = pd.concat(
        [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def compute_features(df: pd.DataFrame, suffix: str) -> pd.DataFrame:
    """
    Compute features for a given timeframe dataframe.
    MUST exactly match the features used during training in build_from_train.py.
    
    Features per timeframe: close, rsi, atr, ma_5, ma_20, ma_50, volatility, returns
    """
    close = df["close"]

    feats = pd.DataFrame(index=df.index)
    feats[f"close_{suffix}"] = close
    feats[f"rsi_{suffix}"] = rsi(close, 14)
    feats[f"atr_{suffix}"] = atr(df, 14)
    feats[f"ma_5_{suffix}"] = close.rolling(5).mean()
    feats[f"ma_20_{suffix}"] = close.rolling(20).mean()
    feats[f"ma_50_{suffix}"] = close.rolling(50).mean()
    feats[f"volatility_{suffix}"] = close.rolling(20).std()
    feats[f"returns_{suffix}"] = close.pct_change()

    return feats


def pip_size(symbol: str) -> float:
    return 0.0001 if "JPY" not in symbol.upper() else 0.01


# ==================== Timeframe Resampling ====================

RESAMPLE_MAP = {
    "5min": "5min",
    "15min": "15min",
    "30min": "30min",
    "1H": "1h",
    "4H": "4h",
}


def resample_ohlcv(df_1min: pd.DataFrame, target_tf: str) -> pd.DataFrame:
    """
    Resample 1-minute OHLCV data to a higher timeframe.
    
    Args:
        df_1min: DataFrame with time, open, high, low, close, volume columns (sorted ascending)
        target_tf: Target timeframe string for pd.resample ('5min', '15min', '30min', '1h', '4h')
    
    Returns:
        Resampled DataFrame with same OHLCV columns
    """
    df = df_1min.copy()
    df = df.set_index('time')

    resampled = df.resample(target_tf).agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()

    resampled = resampled.reset_index()
    return resampled


def build_multitf_from_1min(df_1min: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    Build a multi-timeframe data dict from 1-minute data by resampling.
    
    Args:
        df_1min: 1-minute OHLCV DataFrame with columns [time, open, high, low, close, volume]
    
    Returns:
        Dict mapping timeframe suffix to DataFrame:
        {"1min": df, "5min": df, "15min": df, "30min": df, "1H": df, "4H": df}
    """
    data = {"1min": df_1min.copy()}

    for tf_suffix, resample_freq in RESAMPLE_MAP.items():
        data[tf_suffix] = resample_ohlcv(df_1min, resample_freq)

    return data


def build_multitf_from_api(api_data: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """
    Build multi-timeframe data dict from separately fetched API data.
    Each value should be a DataFrame with [time, open, high, low, close, volume].
    
    Args:
        api_data: Dict mapping interval strings to DataFrames
                  e.g. {"1min": df, "5min": df, ...}
    """
    return api_data


# ==================== Feature Building (Multi-Timeframe) ====================

def build_features_from_data(data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Build multi-timeframe feature matrix from data dict.
    Matches the feature building process in generate_signals_2025.py.
    
    Handles the case where higher timeframes have limited data by:
    1. Computing features normally
    2. Forward-filling NaN values from merge_asof
    3. For remaining NaN (e.g., rolling windows wider than available data),
       filling with the latest available value or column-wise forward fill
    
    Args:
        data: Dict mapping timeframe suffix to OHLCV DataFrame
              Must include at least "1min"
    
    Returns:
        DataFrame with all features computed and merged
    """
    if "1min" not in data:
        raise ValueError("1min data is required as base timeframe")
    
    base = data["1min"].copy()
    
    # Ensure 'time' column is datetime
    if not pd.api.types.is_datetime64_any_dtype(base["time"]):
        base["time"] = pd.to_datetime(base["time"])

    # M1 features
    feat_m1 = compute_features(base, "1min")
    result = pd.concat(
        [base[["time", "open", "high", "low", "close", "volume"]], feat_m1], axis=1
    )

    # Merge other timeframes
    for tf in ["5min", "15min", "30min", "1H", "4H"]:
        if tf not in data:
            print(f"  [GBDT] WARNING: {tf} data missing, skipping")
            continue

        df_tf = data[tf].copy()
        if not pd.api.types.is_datetime64_any_dtype(df_tf["time"]):
            df_tf["time"] = pd.to_datetime(df_tf["time"])

        n_rows = len(df_tf)
        feat_tf = compute_features(df_tf, tf)

        # For higher TFs with limited data, forward-fill within the TF features
        # so that merge_asof has valid values to propagate
        feat_tf = feat_tf.ffill()

        # Merge on time (backward fill - use latest available higher-TF data)
        df_merged = pd.merge_asof(
            result[["time"]],
            pd.concat([df_tf[["time"]], feat_tf], axis=1),
            on="time",
            direction="backward",
        )

        for col in feat_tf.columns:
            result[col] = df_merged[col].values
        
        valid_count = feat_tf.iloc[-1:].notna().sum().sum() if len(feat_tf) > 0 else 0
        total_cols = len(feat_tf.columns)
        print(f"  [GBDT] {tf}: {n_rows} bars, {valid_count}/{total_cols} features valid at last row")

    # Fill remaining NaN values:
    # 1) Forward fill (time-series appropriate)
    result = result.ffill()
    # 2) Backward fill for any remaining NaN at the start
    result = result.bfill()
    
    # Drop only rows where ALL feature columns are NaN (shouldn't happen now)
    feature_cols_in_data = [c for c in result.columns if c not in {"time", "open", "high", "low", "close", "volume"}]
    result = result.dropna(subset=feature_cols_in_data, how='all')
    
    n_rows = len(result)
    n_nan = result[feature_cols_in_data].isna().sum().sum()
    
    if n_nan > 0:
        print(f"  [GBDT] WARNING: {n_nan} NaN values remain after fill, filling with 0")
        result = result.fillna(0)
    
    print(f"  [GBDT] Final feature matrix: {n_rows} rows, {len(result.columns)} columns")

    return result


# ==================== Signal Generator Class ====================

class GBDTSignalGenerator:
    """
    GBDT Signal Generator using the trained multi-timeframe ensemble model.
    
    Model: LightGBM + XGBoost + CatBoost ensemble
    Features: 48 multi-timeframe technical indicators
    Classes: SELL(0), NEUTRAL(1), BUY(2)
    """

    # Configuration from the trained model
    SL_MULT = 5.0       # ATR × 5
    TP_MULT = 15.0       # ATR × 15 (TP = SL × 3, 1:3 risk/reward)
    MIN_SL_PIPS = 15.0   # Minimum SL
    MIN_TP_PIPS = 45.0   # Minimum TP
    CONF_THRESHOLD = 0.60  # Default confidence threshold
    MIN_ATR_PIPS = 4.0   # Minimum ATR filter

    # Class mapping: model output -> signal
    CLASS_MAP = {0: "SELL", 1: "HOLD", 2: "BUY"}

    def __init__(self):
        self.model_path = resolve_model_path()
        self.models = None
        self.feature_cols = None
        self.calibrator = None
        self.is_loaded = False
        self.model_version = "GBDT_unknown"

    def load_models(self) -> bool:
        """Load the trained GBDT ensemble model"""
        try:
            if not os.path.exists(self.model_path):
                print(f"[GBDT] Model file not found: {self.model_path}")
                print("[GBDT] Latest model is required and no fallback model is allowed.")
                print(f"[GBDT] Please export latest model to: {LATEST_MODEL_PATH}")
                print("[GBDT] Or set ACTIVE_GBDT_MODEL_PATH to a promoted model artifact.")
                return False

            print(f"[GBDT] Loading model from {self.model_path}...")
            model_data = joblib.load(self.model_path)

            self.models = model_data["models"]
            self.feature_cols = model_data["feature_cols"]
            self.calibrator = model_data.get("calibrator")

            meta = model_data.get("metadata") or {}
            variant = meta.get("variant")
            if variant:
                self.model_version = str(variant)
            else:
                self.model_version = os.path.splitext(os.path.basename(self.model_path))[0]

            self.is_loaded = True
            print(f"[GBDT] ✓ Model loaded successfully")
            print(f"[GBDT]   Active file: {self.model_path}")
            print(f"[GBDT]   Version: {self.model_version}")
            print(f"[GBDT]   Models: {list(self.models.keys())}")
            print(f"[GBDT]   Features: {len(self.feature_cols)}")
            print(f"[GBDT]   Calibrator: {'Yes' if self.calibrator else 'No'}")
            return True

        except Exception as e:
            print(f"[GBDT] ✗ Error loading model: {e}")
            import traceback
            traceback.print_exc()
            self.is_loaded = False
            return False

    def _predict_ensemble(self, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Get ensemble predictions from all GBDT models.
        
        Returns:
            (pred_class, pred_conf, ensemble_proba)
            - pred_class: predicted class index (0=SELL, 1=HOLD, 2=BUY)
            - pred_conf: confidence score (calibrated if calibrator available)
            - ensemble_proba: raw averaged probability array [n_samples, n_classes]
        """
        all_proba = []
        for name, model in self.models.items():
            proba = model.predict_proba(X)
            all_proba.append(proba)

        # Average probabilities across models
        ensemble_proba = np.mean(all_proba, axis=0)

        # Get predictions
        pred_class = ensemble_proba.argmax(axis=1)
        pred_conf = ensemble_proba.max(axis=1)

        # NOTE: Calibrator disabled — it was outputting constant values (e.g. always 93.4%)
        # regardless of input, which means it was trained incorrectly.
        # Raw ensemble max probability is already a reliable confidence estimate.

        return pred_class, pred_conf, ensemble_proba

    def _calculate_sl_tp(self, atr_value: float, direction: str, entry_price: float, symbol: str = "EURUSD") -> Dict[str, float]:
        """Calculate SL/TP based on ATR (matches training config)"""
        pips = pip_size(symbol)
        atr_pips = atr_value / pips

        sl_pips = max(atr_pips * self.SL_MULT, self.MIN_SL_PIPS)
        tp_pips = max(sl_pips * (self.TP_MULT / self.SL_MULT), self.MIN_TP_PIPS)

        if direction == "BUY":
            stop_loss = entry_price - (sl_pips * pips)
            take_profit = entry_price + (tp_pips * pips)
        else:  # SELL
            stop_loss = entry_price + (sl_pips * pips)
            take_profit = entry_price - (tp_pips * pips)

        risk_reward = round(tp_pips / sl_pips, 2) if sl_pips > 0 else 0

        return {
            "stop_loss": round(stop_loss, 5),
            "take_profit": round(take_profit, 5),
            "sl_pips": round(sl_pips, 1),
            "tp_pips": round(tp_pips, 1),
            "risk_reward": f"1:{risk_reward}",
            "atr_pips": round(atr_pips, 2),
        }

    def check_features(self, df_features: pd.DataFrame) -> Dict[str, Any]:
        """
        Check if the feature DataFrame matches what the model expects.
        
        Returns:
            Dict with compatibility info
        """
        if self.feature_cols is None:
            return {"compatible": False, "error": "Model not loaded"}

        available = set(df_features.columns)
        expected = set(self.feature_cols)

        missing = expected - available
        extra = available - expected - {"time", "open", "high", "low", "close", "volume"}

        return {
            "compatible": len(missing) == 0,
            "expected_features": len(expected),
            "available_features": len(available),
            "missing_features": sorted(list(missing)),
            "extra_features": sorted(list(extra)),
        }

    def generate_signal(
        self,
        df_1min: pd.DataFrame,
        multi_tf_data: Dict[str, pd.DataFrame] = None,
        min_confidence: float = None,
        symbol: str = "EURUSD",
    ) -> Dict[str, Any]:
        """
        Generate trading signal from OHLCV data.
        
        Args:
            df_1min: 1-minute OHLCV DataFrame (at least 200 rows)
            multi_tf_data: Optional pre-fetched multi-timeframe data dict.
                          If None, will resample from 1min data.
            min_confidence: Minimum confidence threshold (default: self.CONF_THRESHOLD)
            symbol: Currency pair symbol
        
        Returns:
            Signal dictionary with direction, confidence, SL/TP, etc.
        """
        if not self.is_loaded:
            return {"error": "Model not loaded", "signal": "HOLD"}

        if df_1min is None or len(df_1min) < 100:
            return {
                "error": f"Need at least 100 rows of 1min data, got {len(df_1min) if df_1min is not None else 0}",
                "signal": "HOLD",
            }

        conf_threshold = min_confidence if min_confidence is not None else self.CONF_THRESHOLD

        try:
            # Ensure column names are lowercase
            df_1min = df_1min.copy()
            df_1min.columns = df_1min.columns.str.lower()
            
            # Ensure time column
            if 'time' not in df_1min.columns and 'datetime' in df_1min.columns:
                df_1min = df_1min.rename(columns={'datetime': 'time'})
            if 'time' not in df_1min.columns and 'timestamp' in df_1min.columns:
                df_1min = df_1min.rename(columns={'timestamp': 'time'})
            
            df_1min['time'] = pd.to_datetime(df_1min['time'])
            df_1min = df_1min.sort_values('time').reset_index(drop=True)

            # Build multi-timeframe data
            if multi_tf_data is not None:
                data = multi_tf_data
                data["1min"] = df_1min
            else:
                # Resample from 1min data
                data = build_multitf_from_1min(df_1min)

            # Build features
            df_features = build_features_from_data(data)

            if len(df_features) == 0:
                return {"error": "No valid data after feature computation", "signal": "HOLD"}

            # Check feature compatibility
            compat = self.check_features(df_features)
            if not compat["compatible"]:
                # Try to add missing features with 0 (shouldn't happen if compute_features matches)
                for col in compat["missing_features"]:
                    df_features[col] = 0
                print(f"[GBDT] WARNING: Added {len(compat['missing_features'])} missing features as zeros: {compat['missing_features'][:5]}...")

            # Extract feature matrix
            X = df_features[self.feature_cols].to_numpy()

            # Predict using ensemble (use last row)
            pred_class, pred_conf, ensemble_proba = self._predict_ensemble(X)

            # Get last row prediction
            last_class = int(pred_class[-1])
            last_conf = float(pred_conf[-1])
            last_proba = ensemble_proba[-1]  # [P(SELL), P(HOLD), P(BUY)]

            signal_type = self.CLASS_MAP.get(last_class, "HOLD")

            # For display: if model predicts BUY/SELL but confidence is low,
            # use the BUY/SELL class confidence (not HOLD confidence)
            buy_conf  = float(last_proba[2])
            sell_conf = float(last_proba[0])
            hold_conf = float(last_proba[1])

            # Dominant directional confidence (BUY or SELL, whichever is higher)
            directional_conf = max(buy_conf, sell_conf)
            directional_type = "BUY" if buy_conf >= sell_conf else "SELL"

            print(f"[GBDT] Raw proba — SELL:{sell_conf*100:.1f}% HOLD:{hold_conf*100:.1f}% BUY:{buy_conf*100:.1f}% | pred={signal_type} conf={last_conf*100:.1f}%")
            last_row = df_features.iloc[-1]
            entry_price = float(last_row["close"])

            # Get ATR for SL/TP
            atr_col = "atr_1min"
            atr_value = float(last_row.get(atr_col, 0.001))

            # ATR filter
            atr_pips = atr_value / pip_size(symbol)

            # Per-model probabilities
            model_probs = {}
            for name, model in self.models.items():
                try:
                    p = model.predict_proba(X[-1:])
                    model_probs[name] = {
                        "SELL": round(float(p[0][0]) * 100, 2),
                        "HOLD": round(float(p[0][1]) * 100, 2),
                        "BUY": round(float(p[0][2]) * 100, 2),
                    }
                except Exception:
                    pass

            # Apply confidence and ATR filters
            if signal_type in ("BUY", "SELL") and last_conf >= conf_threshold and atr_pips >= self.MIN_ATR_PIPS:
                # Valid signal
                sl_tp = self._calculate_sl_tp(atr_value, signal_type, entry_price, symbol)

                return {
                    "signal": signal_type,
                    "confidence": round(last_conf * 100, 2),
                    "entry_price": round(entry_price, 5),
                    "stop_loss": sl_tp["stop_loss"],
                    "take_profit": sl_tp["take_profit"],
                    "sl_pips": sl_tp["sl_pips"],
                    "tp_pips": sl_tp["tp_pips"],
                    "risk_reward": sl_tp["risk_reward"],
                    "atr_pips": sl_tp["atr_pips"],
                    "probabilities": {
                        "SELL": round(float(last_proba[0]) * 100, 2),
                        "HOLD": round(float(last_proba[1]) * 100, 2),
                        "BUY": round(float(last_proba[2]) * 100, 2),
                    },
                    "model_probabilities": model_probs,
                    "timestamp": datetime.now().isoformat(),
                    "target_time": (datetime.now() + timedelta(hours=4)).isoformat(),
                    "horizon_hours": 4,
                    "model_version": self.model_version,
                    "min_confidence_used": round(conf_threshold * 100, 2),
                    "features_used": len(self.feature_cols),
                }
            else:
                # HOLD — model neutral OR confidence too low OR ATR too low
                reason_parts = []
                if signal_type == "HOLD":
                    reason_parts.append("Загвар HOLD (саармаг) таамаглаж байна")
                elif last_conf < conf_threshold:
                    reason_parts.append(
                        f"{signal_type} итгэлцүүр {last_conf*100:.1f}% < босго {conf_threshold*100:.1f}%"
                    )
                if atr_pips < self.MIN_ATR_PIPS:
                    reason_parts.append(
                        f"ATR {atr_pips:.1f} pips — зах зээл тайван байна"
                    )

                # For frontend: expose directional lean even on HOLD
                # so it can show "BUY 45%" trend hint
                return {
                    "signal": "HOLD",
                    # Expose directional confidence for trend hint display
                    "confidence": round(directional_conf * 100, 2),
                    "directional_signal": directional_type,
                    "hold_confidence": round(hold_conf * 100, 2),
                    "entry_price": round(entry_price, 5),
                    "reason": "; ".join(reason_parts) if reason_parts else "Тодорхой сигнал байхгүй",
                    "raw_signal": signal_type,
                    "atr_pips": round(atr_pips, 2),
                    "probabilities": {
                        "SELL": round(float(last_proba[0]) * 100, 2),
                        "HOLD": round(float(last_proba[1]) * 100, 2),
                        "BUY": round(float(last_proba[2]) * 100, 2),
                    },
                    "model_probabilities": model_probs,
                    "timestamp": datetime.now().isoformat(),
                    "target_time": (datetime.now() + timedelta(hours=4)).isoformat(),
                    "horizon_hours": 4,
                    "model_version": self.model_version,
                    "min_confidence_used": round(conf_threshold * 100, 2),
                    "features_used": len(self.feature_cols),
                }

        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": str(e), "signal": "HOLD"}


# ==================== Singleton ====================

_generator_gbdt = None


def get_signal_generator_gbdt() -> GBDTSignalGenerator:
    """Get or create GBDT signal generator singleton"""
    global _generator_gbdt
    if _generator_gbdt is None:
        _generator_gbdt = GBDTSignalGenerator()
        _generator_gbdt.load_models()
    return _generator_gbdt


if __name__ == "__main__":
    print("=" * 60)
    print("Testing GBDT Signal Generator")
    print("=" * 60)

    gen = GBDTSignalGenerator()
    if gen.load_models():
        print(f"\n✓ Model loaded")
        print(f"  Features: {gen.feature_cols[:5]}...")
        print(f"  Models: {list(gen.models.keys())}")

        # Test with sample data
        test_csv = os.path.join(
            PROJECT_ROOT, 'model & backtest result', 'data', 'signal', 'EURUSD_m1.csv'
        )
        if os.path.exists(test_csv):
            df = pd.read_csv(test_csv, parse_dates=["time"])
            df = df.tail(5000).reset_index(drop=True)  # Use last 5000 1min bars
            print(f"\n  Test data: {len(df)} rows")

            signal = gen.generate_signal(df)
            print(f"\n  Signal: {signal.get('signal')}")
            print(f"  Confidence: {signal.get('confidence')}%")
            if signal.get('entry_price'):
                print(f"  Entry: {signal.get('entry_price')}")
                print(f"  SL: {signal.get('sl_pips')} pips")
                print(f"  TP: {signal.get('tp_pips')} pips")
    else:
        print("\n✗ Model load failed")
