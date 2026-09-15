import sys
sys.path.insert(0, "/media/scott/data/git/auto-finetune")
from src.merge import merge_adapter
merge_adapter(
    "/media/scott/data/finetune-staging/outputs/checkpoints/minicpm5-2b-iter3",
    "openbmb/MiniCPM5-2B",
    "/media/scott/data/finetune-staging/models/MiniCPM5-2B-iter3-merged",
    rocm=True,
)
