"""Training frameworks, as recipe backends.

Each module here declares one framework: which tasks it trains, which knobs each
takes, and the notebook cells those knobs become. `base.RecipeBackend` is the
contract; `base.all_backends()` is the lookup, which merges these built-ins with
anything a backend plugin registered through `host.add_recipe_backend`.

Order matters: it is the order tasks appear in the form, and `backend_for_task`
resolves a task named without a backend to the first one that claims it — so trl
stays the default for `sft` no matter what else is installed.
"""

from backend.modules.training.backends.pretrain_backend import (
    NANOTRON,
    TORCHTITAN,
)
from backend.modules.training.backends.trl_backend import BACKEND as TRL
from backend.modules.training.backends.unsloth_backend import BACKEND as UNSLOTH

BUILTIN = (TRL, UNSLOTH, TORCHTITAN, NANOTRON)

__all__ = ["BUILTIN", "NANOTRON", "TORCHTITAN", "TRL", "UNSLOTH"]
