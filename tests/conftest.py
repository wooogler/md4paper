"""테스트 격리 — 사용자의 실제 ~/.config/md4paper 설정에 영향받지 않도록.

config.CONFIG_DIR은 임포트 시점에 env를 읽으므로, 테스트 모듈보다 먼저 로드되는
conftest에서 env를 지정해야 한다.
"""

import os
import tempfile

os.environ["MD4PAPER_CONFIG_DIR"] = tempfile.mkdtemp(prefix="md4paper-test-config-")

import pytest  # noqa: E402 — env 설정 후 임포트해야 함


@pytest.fixture(autouse=True)
def _clean_heading_prefs():
    """헤더 선택 학습은 전역 파일에 쌓이므로 테스트마다 비운다.

    (controller.save()가 선택을 학습하기 때문에, 안 비우면 다음 테스트의 레벨이 바뀐다.)
    """
    from md4paper import prefs

    if prefs.PREFS_PATH.exists():
        prefs.PREFS_PATH.unlink()
    yield
    if prefs.PREFS_PATH.exists():
        prefs.PREFS_PATH.unlink()


@pytest.fixture(autouse=True)
def _clean_config():
    """config.toml(키·Global 기본설정)은 세션 공유 CONFIG_DIR에 쌓이므로 테스트마다 비운다.

    (홈 Global 설정을 config에 저장하면 build()가 그 값을 시드해 다른 테스트의 기본값이 바뀐다.)
    """
    from md4paper import config

    if config.CONFIG_PATH.exists():
        config.CONFIG_PATH.unlink()
    yield
    if config.CONFIG_PATH.exists():
        config.CONFIG_PATH.unlink()
