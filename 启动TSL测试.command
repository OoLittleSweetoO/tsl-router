#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ -x /opt/homebrew/bin/python3 ]]; then
    /opt/homebrew/bin/python3 tally_sender_ui.py
else
    python3 tally_sender_ui.py
fi
if [[ $? -ne 0 ]]; then
    echo '启动失败，请确认 Python 已安装 Tkinter。/ Python with Tkinter is required.'
    read -r '?按回车关闭 / Press Enter to close'
fi
