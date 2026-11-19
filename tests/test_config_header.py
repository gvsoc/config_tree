"""Tests for config_header: C defines of the readable fields of a config tree."""

from typing import Annotated

import pytest

from config_tree import Config, cfg_field
from config_tree.config_header import config_defines, generate_config_header


class Runtime:
    """Same name as the gvrun marker of the fields set at run time."""


class L2Config(Config):
    size: int = cfg_field(default=0x400000, read=True, fmt="hex", desc="L2 size")
    banks: int = cfg_field(default=4, read=True)
    hidden: int = cfg_field(default=7)
    tunable: int = cfg_field(default=3, read=True, write=True)
    stim: Annotated[str, Runtime] = cfg_field(default="file.bin", read=True)
    items: list = cfg_field(default_factory=lambda: [1, 2], read=True)


class SocConfig(Config):
    l2: L2Config = cfg_field(init=False)

    def __post_init__(self):
        super().__post_init__()
        self.l2 = L2Config("l2")


class BoardConfig(Config):
    soc: SocConfig = cfg_field(init=False)
    core: str = cfg_field(default="ri5ky", read=True)
    secure: bool = cfg_field(default=True, read=True)
    base: int = cfg_field(default=0x80000000, read=True)

    def __post_init__(self):
        super().__post_init__()
        self.soc = SocConfig("soc")


@pytest.mark.parametrize("root_name", ["evk", "/"])
def test_names_relative_to_root(root_name):
    # gvrun names the root after the board, the make flow '/': same defines
    board = BoardConfig(root_name)
    defines = {name: value for name, value, _ in config_defines(board)}
    assert defines == {
        "CONFIG_CORE": '"ri5ky"',
        "CONFIG_SECURE": "1",
        "CONFIG_BASE": "0x80000000",
        "CONFIG_SOC_L2_SIZE": "0x400000",
        "CONFIG_SOC_L2_BANKS": "4",
    }


def test_subtree_keeps_root_names():
    board = BoardConfig("evk")
    defines = [name for name, _, _ in config_defines(board.soc)]
    assert defines == ["CONFIG_SOC_L2_SIZE", "CONFIG_SOC_L2_BANKS"]


def test_nodes_restrict():
    board = BoardConfig("evk")
    defines = [name for name, _, _ in config_defines(board, nodes=[board])]
    assert defines == ["CONFIG_CORE", "CONFIG_SECURE", "CONFIG_BASE"]


def test_header():
    board = BoardConfig("evk")
    header = generate_config_header(board)
    assert "#ifndef TARGET_CONFIG_H" in header
    assert "/* L2 size */" in header
    assert "#define CONFIG_SOC_L2_SIZE" in header
    assert "HIDDEN" not in header and "TUNABLE" not in header and "STIM" not in header


def test_duplicate_names():
    class DupConfig(Config):
        a_b: int = cfg_field(default=1, read=True)

    class TopConfig(Config):
        a: DupConfig = cfg_field(init=False)
        a_a_b: int = cfg_field(default=2, read=True)

        def __post_init__(self):
            super().__post_init__()
            self.a = DupConfig("a")

    with pytest.raises(ValueError, match="CONFIG_A_A_B"):
        config_defines(TopConfig("top"))
