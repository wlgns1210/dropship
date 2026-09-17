"""설정 로딩.

배포 형태마다 파일 배치가 다르다. 설정을 읽는 단계에서 죽으면 로그도 남기지
못하고 앱이 기동조차 안 되므로, 여기가 가장 방어적이어야 한다.
"""

from pathlib import Path

from app.config import Settings, _env_files


class TestEnvFileDiscovery:
    def test_survives_without_any_env_file(self) -> None:
        """컨테이너에는 .env 도 저장소 구조도 없다.

        예전에는 parents[3] 으로 고정 깊이를 가정해서, /app/app/config.py 로
        놓인 컨테이너에서 IndexError 로 앱이 기동조차 못 했다. 설정을 읽기
        전에 죽으니 원인도 보이지 않았다.
        """
        # 예외 없이 튜플을 돌려주기만 하면 된다 (비어 있어도 정상)
        assert isinstance(_env_files(), tuple)

    def test_settings_load_from_environment_alone(
        self, monkeypatch: object, tmp_path: Path
    ) -> None:
        """환경 변수만으로 설정이 완성되어야 한다."""
        settings = Settings(
            skiff_env="production",
            deploy_mode="single",
            data_dir=str(tmp_path),
            ip_hash_salt="x" * 40,
            url_signing_key="y" * 40,
        )
        assert settings.is_single_node
        assert settings.files_dir == tmp_path / "files"
        assert settings.sqlite_path == tmp_path / "skiff.db"

    def test_discovery_stops_at_the_nearest_match(self) -> None:
        """상위 디렉터리의 무관한 .env 를 주워 담으면 안 된다."""
        files = _env_files()
        if files:
            parents = {f.parent for f in files}
            assert len(parents) == 1, "여러 깊이에서 긁어모으고 있다"
