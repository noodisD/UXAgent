"""Single source of truth for Hydra config loading."""
import pathlib

from hydra import compose, initialize_config_dir
from omegaconf import DictConfig


def load_cfg(config_name: str = "base") -> DictConfig:
    conf_dir = pathlib.Path(__file__).resolve().parents[2] / "conf"
    with initialize_config_dir(version_base=None, config_dir=str(conf_dir)):
        return compose(config_name=config_name)
