from pathlib import Path
from invoke import Config


def create_environment_configuration(base_dir: Path) -> Config:
    config = Config(project_location=base_dir, lazy=True)
    config.load_system()
    config.load_user()
    config.load_project()
    config.load_runtime()
    config.load_shell_env()
    config.merge()
    return config
