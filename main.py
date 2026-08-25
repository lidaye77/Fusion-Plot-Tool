import faulthandler
import logging
import os
import sys
import threading
import traceback
from datetime import datetime

from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ui import MainWindow, set_dark_theme


def _build_log_path():

    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "runtime_error.log"
    )


def _resource_path(relative_path):

    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def _setup_runtime_logging():

    log_path = _build_log_path()

    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        encoding="utf-8"
    )

    logging.info("==== Application launch ====")

    fault_log_file = open(log_path, "a", encoding="utf-8")
    faulthandler.enable(file=fault_log_file, all_threads=True)

    def _sys_excepthook(exc_type, exc_value, exc_tb):

        logging.error(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb)
        )

    def _thread_excepthook(args):

        logging.error(
            "Unhandled thread exception",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
        )

    def _qt_message_handler(msg_type, _context, message):

        level_map = {
            QtMsgType.QtDebugMsg: logging.DEBUG,
            QtMsgType.QtInfoMsg: logging.INFO,
            QtMsgType.QtWarningMsg: logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg: logging.CRITICAL,
        }
        level = level_map.get(msg_type, logging.INFO)
        logging.log(level, "Qt: %s", message)

    sys.excepthook = _sys_excepthook
    threading.excepthook = _thread_excepthook
    qInstallMessageHandler(_qt_message_handler)

    return log_path


def main():

    log_path = _setup_runtime_logging()

    app = QApplication(sys.argv)
    app_icon = QIcon(_resource_path(os.path.join("assets", "fusionplot.ico")))
    app.setWindowIcon(app_icon)

    # 设置深色主题
    set_dark_theme(app)

    try:
        window = MainWindow()
    except Exception:
        logging.error("MainWindow init failed\n%s", traceback.format_exc())
        raise

    window.setWindowIcon(app_icon)

    window.show()

    exit_code = app.exec()
    logging.info("Application exit code: %s", exit_code)
    logging.info("Runtime log path: %s", log_path)
    sys.exit(exit_code)


if __name__ == "__main__":

    main()