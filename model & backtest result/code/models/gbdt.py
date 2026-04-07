from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple, Optional

import numpy as np


def _try_import_lightgbm():
    try:
        import lightgbm as lgb

        return lgb
    except Exception:
        return None


def _try_import_xgboost():
    try:
        import xgboost as xgb

        return xgb
    except Exception:
        return None


def _try_import_catboost():
    try:
        from catboost import CatBoostClassifier

        return CatBoostClassifier
    except Exception:
        return None


def fit_models(
    X: np.ndarray,
    y: np.ndarray,
    random_state: int,
    pos_weight: float,
    X_val: np.ndarray = None,
    y_val: np.ndarray = None,
) -> Dict[str, object]:
    """
    Train GBDT models with REGULARIZATION + EARLY STOPPING to prevent overfitting.
    
    Key improvements:
    - Reduced model capacity (max_depth, num_leaves)
    - L1/L2 regularization (reg_alpha, reg_lambda)
    - Early stopping on validation set
    - Lower learning rate for better generalization
    """
    models: Dict[str, object] = {}

    is_multiclass = len(np.unique(y)) > 2
    num_classes = int(len(np.unique(y)))

    lgb = _try_import_lightgbm()
    if lgb is not None:
        # PHASE 7B: Anti-overfitting configuration + GPU
        lgb_params = dict(
            n_estimators=500,  # More trees but with early stopping
            learning_rate=0.03,  # Slower learning (was 0.05)
            max_depth=6,  # Limited depth (was -1 unlimited!)
            num_leaves=31,  # Much less leaves (was 128!)
            subsample=0.7,  # More dropout (was 0.8)
            colsample_bytree=0.7,  # More feature sampling
            reg_alpha=0.1,  # L1 regularization
            reg_lambda=1.0,  # L2 regularization (stronger)
            min_child_samples=20,  # Prevent tiny leaves
            random_state=random_state,
            device='gpu',  # GPU acceleration
            verbose=-1,
        )

        if is_multiclass:
            lgb_params['objective'] = 'multiclass'
            lgb_params['num_class'] = num_classes
            lgb_params['class_weight'] = 'balanced'
        else:
            lgb_params['scale_pos_weight'] = pos_weight

        model = lgb.LGBMClassifier(**lgb_params)
        
        if X_val is not None and y_val is not None:
            # Early stopping on validation set
            model.fit(
                X, y,
                eval_set=[(X_val, y_val)],
                callbacks=[lgb.early_stopping(50, verbose=False)]
            )
            print(f"    LightGBM: {model.best_iteration_} trees (early stopped)")
        else:
            model.fit(X, y)
        
        models["lightgbm"] = model

    xgb = _try_import_xgboost()
    if xgb is not None:
        # PHASE 7B: Anti-overfitting configuration
        xgb_params = dict(
            n_estimators=500,
            learning_rate=0.03,  # Slower (was 0.05)
            max_depth=5,  # Shallower trees (was 8)
            subsample=0.7,
            colsample_bytree=0.7,
            reg_alpha=0.1,  # L1 regularization
            reg_lambda=1.0,  # L2 regularization
            min_child_weight=5,  # Prevent overfitting
            gamma=0.1,  # Min loss reduction for split
            eval_metric="mlogloss" if is_multiclass else "logloss",
            random_state=random_state,
            tree_method='hist',  # Fast histogram-based (CPU)
            verbosity=0,
        )

        if is_multiclass:
            xgb_params['objective'] = 'multi:softprob'
            xgb_params['num_class'] = num_classes
        else:
            xgb_params['objective'] = 'binary:logistic'
            xgb_params['scale_pos_weight'] = pos_weight

        model = xgb.XGBClassifier(**xgb_params)
        
        if X_val is not None and y_val is not None:
            # Early stopping on validation set (new syntax)
            model.fit(
                X, y,
                eval_set=[(X_val, y_val)],
                verbose=False
            )
            if hasattr(model, 'best_iteration'):
                print(f"    XGBoost: {model.best_iteration} trees (early stopped)")
        else:
            model.fit(X, y)
        
        models["xgboost"] = model

    catboost = _try_import_catboost()
    if catboost is not None:
        # PHASE 7B: Anti-overfitting configuration + GPU
        model = catboost(
            iterations=500,
            learning_rate=0.03,  # Slower (was 0.05)
            depth=5,  # Shallower (was 8)
            l2_leaf_reg=3.0,  # L2 regularization (stronger)
            bagging_temperature=1.0,  # Bayesian bootstrap
            random_strength=1.0,  # Randomness in splits
            loss_function="MultiClass",  # 3-class: BUY/SELL/HOLD
            task_type='GPU',  # GPU acceleration
            devices='0',  # RTX 5060
            verbose=False,
            random_seed=random_state,
        )
        
        if X_val is not None and y_val is not None:
            # Early stopping on validation set
            model.fit(
                X, y,
                eval_set=(X_val, y_val),
                early_stopping_rounds=50,
                verbose=False
            )
            print(f"    CatBoost: {model.best_iteration_} trees (early stopped)")
        else:
            model.fit(X, y)
        
        models["catboost"] = model

    if not models:
        raise RuntimeError("No GBDT models available. Install lightgbm/xgboost/catboost.")

    return models


def predict_proba(models: Dict[str, object], X: np.ndarray) -> np.ndarray:
    """Return ensemble probability for the target trade class.

    For multiclass models (SELL/HOLD/BUY => 0/1/2), BUY (class 2) is used as
    the positive class by default so downstream calibration/evaluation stays
    mathematically consistent.
    """
    probs = []
    for model in models.values():
        p_all = model.predict_proba(X)
        if p_all.ndim == 1:
            probs.append(p_all)
            continue

        classes = np.array(getattr(model, 'classes_', np.arange(p_all.shape[1])))
        if p_all.shape[1] == 2:
            pos_index = 1
        else:
            if 2 in classes:
                pos_index = int(np.where(classes == 2)[0][0])
            else:
                pos_index = p_all.shape[1] - 1

        probs.append(p_all[:, pos_index])

    if not probs:
        raise RuntimeError("No model probabilities produced.")

    return np.mean(np.vstack(probs), axis=0)
