"""Tabular Feature Engineering duel: agents submit feature engineering code to maximize ROC-AUC.
"""

from __future__ import annotations

import random as _random
from typing import Any
import pandas as pd
import numpy as np

from backend.games_engine import verify
from backend.games_engine.base import (
    TERMINAL,
    WORK,
    Action,
    GameSpec,
    GameState,
    register_game,
)

MOVE_TIMEOUT_S = 600.0
MAX_CODE_CHARS = 20_000

DEFAULT_TASKS: list[dict[str, Any]] = [
    {
        "id": "fe-space-mining",
        "name": "Space Mining Expedition Success",
        "description": "Predict whether a deep space asteroid-mining expedition will succeed (binary classification). Involves missing numeric values, high-cardinality categories, and non-linear interactions.",
        "type": "classification",
        "metric": "roc_auc",
        "starter_code": (
            "import pandas as pd\n"
            "import numpy as np\n\n"
            "def transform(df: pd.DataFrame) -> pd.DataFrame:\n"
            "    # Make a copy to avoid SettingWithCopyWarning\n"
            "    df = df.copy()\n"
            "    \n"
            "    # Fill missing values\n"
            "    if 'distance' in df.columns:\n"
            "        df['distance'] = df['distance'].fillna(df['distance'].median() if not df['distance'].isnull().all() else 600.0)\n"
            "    if 'fuel_density' in df.columns:\n"
            "        df['fuel_density'] = df['fuel_density'].fillna(df['fuel_density'].median() if not df['fuel_density'].isnull().all() else 0.8)\n"
            "        \n"
            "    # Map simple categorical features to integers\n"
            "    exp_map = {'rookie': 0, 'veteran': 1, 'elite': 2}\n"
            "    if 'crew_experience' in df.columns:\n"
            "        df['crew_experience'] = df['crew_experience'].map(exp_map).fillna(0)\n"
            "        \n"
            "    ship_map = {'light_freighter': 0, 'heavy_cruiser': 1, 'mining_barge': 2}\n"
            "    if 'ship_class' in df.columns:\n"
            "        df['ship_class'] = df['ship_class'].map(ship_map).fillna(0)\n"
            "        \n"
            "    # Drop target if present\n"
            "    if 'target' in df.columns:\n"
            "        df = df.drop(columns=['target'])\n"
            "        \n"
            "    return df\n"
        )
    }
]

EVALUATE_SCRIPT = """import sys
import os
sys.path.insert(0, os.getcwd())
import json
import pandas as pd
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score

def main():
    try:
        from solution import transform
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"Import error: {e}"}))
        return

    try:
        train_df = pd.read_csv('train.csv')
        test_df = pd.read_csv('test.csv')
        
        y_train = train_df['target']
        y_test = test_df['target']
        
        # Run transform
        try:
            X_train = transform(train_df)
            X_test = transform(test_df)
        except Exception as e:
            print(json.dumps({"ok": False, "error": f"Runtime error in transform(): {e}"}))
            return
            
        # Validations
        if not isinstance(X_train, pd.DataFrame) or not isinstance(X_test, pd.DataFrame):
            print(json.dumps({"ok": False, "error": "transform() must return a pandas DataFrame"}))
            return
            
        if len(X_train) != len(train_df):
            print(json.dumps({"ok": False, "error": f"X_train length mismatch: expected {len(train_df)}, got {len(X_train)}"}))
            return
            
        if len(X_test) != len(test_df):
            print(json.dumps({"ok": False, "error": f"X_test length mismatch: expected {len(test_df)}, got {len(X_test)}"}))
            return
            
        # Check for missing values
        if X_train.isnull().any().any() or X_test.isnull().any().any():
            print(json.dumps({"ok": False, "error": "Transformed DataFrame contains NaNs/null values. Impute all missing values."}))
            return
            
        # Check that all columns are numeric
        for col in X_train.columns:
            if not np.issubdtype(X_train[col].dtype, np.number):
                print(json.dumps({"ok": False, "error": f"Column '{col}' is not numeric. Encode all categorical variables."}))
                return
                
        # Fit model
        clf = GradientBoostingClassifier(random_state=42, n_estimators=100)
        clf.fit(X_train, y_train)
        
        probs = clf.predict_proba(X_test)[:, 1]
        score = roc_auc_score(y_test, probs)
        
        print(json.dumps({
            "ok": True,
            "score": float(score),
            "features": list(X_train.columns)
        }))
        
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"Unexpected grading error: {e}"}))

if __name__ == '__main__':
    main()
"""


class TabularFE(GameState):
    def __init__(self, task: dict[str, Any] | None = None, seed: int = 42) -> None:
        self.task = task or DEFAULT_TASKS[0]
        self.seed = seed
        self.submissions: list[str | None] = [None, None]
        self.reports: list[dict[str, Any]] | None = None

    def current_players(self) -> list[int]:
        return [s for s in (0, 1) if self.submissions[s] is None]

    def current_player(self) -> int:
        if self.current_players():
            return self.current_players()[0]
        return TERMINAL if self.reports is not None else WORK

    def legal_actions(self, player: int) -> list[Action]:
        if player not in self.current_players():
            return []
        return [
            Action(
                id="submit",
                label="submit your transform.py",
                params={"payload": "code", "max_code_chars": MAX_CODE_CHARS},
            )
        ]

    def apply_action(self, player: int, action_id: str, payload: Any = None) -> None:
        if player not in self.current_players():
            raise ValueError("this seat has already submitted")
        if action_id != "submit":
            raise ValueError(f"bad action id {action_id!r}")
        code = payload.get("code") if isinstance(payload, dict) else payload
        self.submissions[player] = str(code or "")[:MAX_CODE_CHARS]

    def _generate_space_mining_data(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        np.random.seed(self.seed)
        n_samples = 800
        
        distance = np.random.uniform(50, 1500, n_samples)
        nan_mask = np.random.rand(n_samples) < 0.15
        distance[nan_mask] = np.nan
        
        fuel_density = np.random.uniform(0.1, 2.0, n_samples)
        nan_mask_2 = np.random.rand(n_samples) < 0.05
        fuel_density[nan_mask_2] = np.nan
        
        crew_experience = np.random.choice(['rookie', 'veteran', 'elite'], n_samples, p=[0.4, 0.4, 0.2])
        ship_class = np.random.choice(['light_freighter', 'heavy_cruiser', 'mining_barge'], n_samples, p=[0.5, 0.3, 0.2])
        anomaly_type = np.random.choice(['gravitational', 'magnetic', 'solar_flare', 'none'], n_samples, p=[0.2, 0.2, 0.1, 0.5])
        
        prob = np.full(n_samples, 0.4)
        
        d_clean = np.where(np.isnan(distance), 600.0, distance)
        prob -= (d_clean > 1000) * 0.3
        prob += (d_clean < 300) * 0.2
        
        prob += (crew_experience == 'elite') * 0.3
        prob += (crew_experience == 'veteran') * 0.1
        prob -= (crew_experience == 'rookie') * 0.15
        
        prob += (ship_class == 'heavy_cruiser') * 0.15
        prob -= (ship_class == 'light_freighter') * 0.1
        
        for i in range(n_samples):
            if anomaly_type[i] != 'none':
                if crew_experience[i] == 'rookie':
                    prob[i] -= 0.35
                elif crew_experience[i] == 'elite':
                    prob[i] += 0.1
                    
        prob = np.clip(prob, 0.01, 0.99)
        target = np.random.binomial(1, prob)
        
        df = pd.DataFrame({
            'distance': distance,
            'fuel_density': fuel_density,
            'crew_experience': crew_experience,
            'ship_class': ship_class,
            'anomaly_type': anomaly_type,
            'target': target
        })
        
        train_df = df.iloc[:500].copy()
        test_df = df.iloc[500:].copy()
        return train_df, test_df

    def run_work(self) -> None:
        reports = []
        train_df, test_df = self._generate_space_mining_data()
        train_csv = train_df.to_csv(index=False)
        test_csv = test_df.to_csv(index=False)

        for seat in (0, 1):
            result = verify.run_python_job(
                {
                    "solution.py": self.submissions[seat] or "",
                    "train.csv": train_csv,
                    "test.csv": test_csv,
                    "evaluate.py": EVALUATE_SCRIPT,
                },
                entry=["evaluate.py"]
            )
            
            score = 0.0
            ok = result.green
            error_message = ""
            try:
                import json
                output_clean = result.stdout.strip()
                json_start = output_clean.find('{')
                if json_start != -1:
                    data = json.loads(output_clean[json_start:])
                    if data.get("ok"):
                        score = data.get("score", 0.0)
                        ok = True
                    else:
                        ok = False
                        error_message = data.get("error", "Unknown error")
                else:
                    ok = False
                    error_message = "Evaluation script did not return a JSON block"
            except Exception as e:
                ok = False
                error_message = f"Failed to parse grading output: {e}"

            reports.append(
                {
                    "ok": ok,
                    "score": score,
                    "error": error_message,
                    "output": (result.stdout + result.stderr)[-2000:],
                }
            )
        self.reports = reports

    def observation(self, player: int) -> dict[str, Any]:
        train_df, _ = self._generate_space_mining_data()
        samples = train_df.head(5).to_dict(orient="records")
        return {
            "game": "tabular_fe",
            "seat": player,
            "task_name": self.task["name"],
            "description": self.task["description"],
            "metric": self.task["metric"],
            "data_samples": samples,
            "starter_code": self.task["starter_code"],
            "submitted": [s is not None for s in self.submissions],
        }

    def public_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "game": "tabular_fe",
            "task_id": self.task["id"],
            "task_name": self.task["name"],
            "description": self.task["description"],
            "submitted": [s is not None for s in self.submissions],
            "grading": self.current_player() == WORK,
            "turn": None,
            "winner": self._winner() if self.is_terminal() else None,
        }
        if self.is_terminal() and self.reports is not None:
            state["reports"] = self.reports
            state["solutions"] = [s or "" for s in self.submissions]
        return state

    def _winner(self) -> int | None:
        if self.reports is None:
            return None
        ok0, ok1 = self.reports[0]["ok"], self.reports[1]["ok"]
        if ok0 and not ok1:
            return 0
        if ok1 and not ok0:
            return 1
        if not ok0 and not ok1:
            return None
        
        s0, s1 = self.reports[0]["score"], self.reports[1]["score"]
        if s0 == s1:
            return None
        return 0 if s0 > s1 else 1

    def returns(self) -> dict[int, float]:
        w = self._winner()
        if w is None:
            return {0: 0.0, 1: 0.0}
        return {w: 1.0, 1 - w: -1.0}


SPEC = register_game(
    GameSpec(
        id="tabular_fe",
        name="Tabular Feature Engineering",
        min_players=2,
        max_players=2,
        factory=TabularFE,
        move_timeout_s=MOVE_TIMEOUT_S,
    )
)
