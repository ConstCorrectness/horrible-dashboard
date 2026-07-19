"""Tests for the Tabular Feature Engineering game engine."""

from __future__ import annotations

from backend.games_engine.base import TERMINAL, WORK
from backend.games_engine.tabular_fe import TabularFE, DEFAULT_TASKS


def test_tabular_fe_flow(monkeypatch) -> None:
    # Ensure code execution is enabled for testing
    monkeypatch.setenv("GAMES_ENABLE_CODE_EXEC", "1")

    game = TabularFE(task=DEFAULT_TASKS[0], seed=42)
    assert sorted(game.current_players()) == [0, 1]

    # Player 0: basic map template (ignores anomaly_type)
    code_0 = (
        "import pandas as pd\n"
        "import numpy as np\n"
        "def transform(df):\n"
        "    df = df.copy()\n"
        "    if 'distance' in df.columns:\n"
        "        df['distance'] = df['distance'].fillna(600.0)\n"
        "    if 'fuel_density' in df.columns:\n"
        "        df['fuel_density'] = df['fuel_density'].fillna(0.8)\n"
        "    exp_map = {'rookie': 0, 'veteran': 1, 'elite': 2}\n"
        "    if 'crew_experience' in df.columns:\n"
        "        df['crew_experience'] = df['crew_experience'].map(exp_map).fillna(0)\n"
        "    ship_map = {'light_freighter': 0, 'heavy_cruiser': 1, 'mining_barge': 2}\n"
        "    if 'ship_class' in df.columns:\n"
        "        df['ship_class'] = df['ship_class'].map(ship_map).fillna(0)\n"
        "    # Drops anomaly_type completely\n"
        "    if 'target' in df.columns:\n"
        "        df = df.drop(columns=['target'])\n"
        "    # Select only numeric\n"
        "    df = df.select_dtypes(include=[np.number])\n"
        "    return df\n"
    )

    # Player 1: advanced template with one-hot encoding (better features!)
    code_1 = (
        "import pandas as pd\n"
        "import numpy as np\n"
        "def transform(df):\n"
        "    df = df.copy()\n"
        "    if 'distance' in df.columns:\n"
        "        df['distance'] = df['distance'].fillna(600.0)\n"
        "    if 'fuel_density' in df.columns:\n"
        "        df['fuel_density'] = df['fuel_density'].fillna(0.8)\n"
        "    # One-hot encode categoricals\n"
        "    df = pd.get_dummies(df, columns=['crew_experience', 'ship_class', 'anomaly_type'], drop_first=True)\n"
        "    # Ensure all boolean outputs from get_dummies are numeric (int/float) for sklearn\n"
        "    for col in df.columns:\n"
        "        if df[col].dtype == bool:\n"
        "            df[col] = df[col].astype(int)\n"
        "    if 'target' in df.columns:\n"
        "        df = df.drop(columns=['target'])\n"
        "    df = df.select_dtypes(include=[np.number])\n"
        "    return df\n"
    )

    game.apply_action(0, "submit", {"code": code_0})
    game.apply_action(1, "submit", {"code": code_1})

    assert game.current_player() == WORK

    # Run the grading task
    game.run_work()

    assert game.current_player() == TERMINAL

    r0, r1 = game.reports
    assert r0["ok"] is True, (
        f"Player 0 evaluation failed: {r0['error']}\\nOutput:\\n{r0['output']}"
    )
    assert r1["ok"] is True, (
        f"Player 1 evaluation failed: {r1['error']}\\nOutput:\\n{r1['output']}"
    )

    # Player 1 should have a strictly higher score because they handled anomaly_type
    assert r1["score"] > r0["score"], (
        f"Scores did not match expectations: r0={r0['score']}, r1={r1['score']}"
    )
    assert game._winner() == 1
    assert game.returns() == {0: -1.0, 1: 1.0}
