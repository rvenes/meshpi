from __future__ import annotations

import logging
import os

from meshpi.daemon import configure_logging


def test_configure_logging_rotates_private_file(tmp_path):
    log_file = tmp_path / "logs" / "meshpi.log"
    try:
        configure_logging(
            "INFO",
            log_file=log_file,
            log_max_bytes=1024,
            log_backup_count=2,
        )
        logger = logging.getLogger("meshpi.rotation-test")
        for index in range(100):
            logger.info("logglinje %s %s", index, "x" * 80)
        for handler in logging.getLogger().handlers:
            handler.flush()

        assert log_file.is_file()
        assert log_file.with_name("meshpi.log.1").is_file()
        assert len(list(log_file.parent.glob("meshpi.log*"))) <= 3
        if os.name == "posix":
            assert log_file.stat().st_mode & 0o777 == 0o600
    finally:
        logging.shutdown()
        for handler in logging.getLogger().handlers[:]:
            logging.getLogger().removeHandler(handler)
