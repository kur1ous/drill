import pytest

from drill.config import ConfigError, load_config
from drill.fsrs import DEFAULT_WEIGHTS


@pytest.mark.parametrize("index, low, high", [
    (0, 0.01, 100), (1, 0.01, 100), (2, 0.01, 100), (3, 0.01, 100),
    (4, 1, 10), (5, 0, 5), (6, 0, 5), (7, 0, 1), (8, 0, 5),
    (9, 0, 1), (10, 0, 5), (11, 0.01, 5), (12, 0, 1), (13, 0, 1),
    (14, 0, 5), (15, 0, 1), (16, 1, 5),
])
def test_weight_bounds(tmp_path, index, low, high):
    path = tmp_path / ".drill.toml"
    weights = list(DEFAULT_WEIGHTS)
    for value in (low, high):
        weights[index] = value
        path.write_text(f"weights = {weights}\n", encoding="utf-8")
        assert load_config(tmp_path)["weights"] == tuple(weights)
    for value in (low - 0.01, high + 0.01):
        weights[index] = value
        path.write_text(f"weights = {weights}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match=rf"weights\[{index}\]"):
            load_config(tmp_path)


def test_default_weights_are_valid_config(tmp_path):
    (tmp_path / ".drill.toml").write_text(f"weights = {list(DEFAULT_WEIGHTS)}\n", encoding="utf-8")
    assert load_config(tmp_path)["weights"] == DEFAULT_WEIGHTS
