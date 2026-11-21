"""The code-review-graph CLI (optional): it builds the knowledge graph of a working tree into a data folder, as an SQLite
database (graph.db) the adaptor then reads through the SQLite gateway."""
import subprocess

from ..domain.errors import HeatmapError


class CodeReviewGraphGateway:
    def __init__(self, executable):
        self.executable = executable  # resolved by the composition root (PATH, else ~/.local/bin)

    def build(self, repo_root, data_dir):
        """Build (or update) the graph of the working tree at `repo_root` into `data_dir`."""
        try:
            subprocess.run([self.executable, "build", "--repo", repo_root, "--data-dir", data_dir, "-q"],
                           capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError) as e:
            raise HeatmapError("code-review-graph build failed (install it, or use --graph builtin): %s"
                               % (getattr(e, "stderr", None) or e))
