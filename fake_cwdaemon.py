#!/usr/bin/env python3
# FT-991A Companion
# Copyright (C) 2026  Charles W. Powell, NK8O
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A simulated cwdaemon, for trying the panel's Keyboard page without a radio or the real program.

The real cwdaemon (https://github.com/acerion/cwdaemon, GPL) runs on the radio's computer and keys the
radio through the DTR line of a serial port. This simulation understands the same UDP requests on
port 6789 (or --port), keys nothing, and works out how long each message would take:

    text                a message to send
    ESC 0               reset to the defaults
    ESC 2 <wpm>         set the speed (4 to 60)
    ESC 4               abort the message that is being sent
    ESC h <token>       when the text that follows has finished, reply "h<token>" + CR LF

    python3 fake_cwdaemon.py --port 6789
"""
import argparse
import socket
import threading
import time

ESC = "\x1b"


class FakeCwDaemon:
    def __init__(self, seconds_per_dot_unit=1.2):
        self.unit = seconds_per_dot_unit      # a dot lasts 1.2 / wpm seconds
        self.wpm = 24
        self.busy_until = 0.0                 # when the queued text will have been sent
        self.reply = None                     # the token to send back after the next text
        self.timers = []
        self.log = []                         # ("text", text, wpm) / ("speed", wpm) / ("abort",) / ("reset",)
        self.lock = threading.Lock()
        self.sock = None

    def duration(self, text):
        return len(text) * 9 * self.unit / self.wpm        # the same generous estimate as the panel

    def handle(self, data, addr):
        text = data.decode("ascii", "ignore")
        with self.lock:
            now = time.monotonic()
            if text.startswith(ESC):
                code, arg = text[1:2], text[2:]
                if code == "0":
                    self.wpm = 24
                    self.log.append(("reset",))
                elif code == "2" and arg.isdigit() and 4 <= int(arg) <= 60:
                    self.wpm = int(arg)
                    self.log.append(("speed", self.wpm))
                elif code == "4":
                    for t in self.timers:
                        t.cancel()
                    self.timers.clear()
                    self.busy_until, self.reply = now, None
                    self.log.append(("abort",))
                elif code == "h":
                    self.reply = (addr, "h" + arg)
                return
            text = text.strip()
            if not text:
                return
            self.log.append(("text", text, self.wpm))
            self.busy_until = max(now, self.busy_until) + self.duration(text)
            if self.reply:
                target, token = self.reply
                self.reply = None
                t = threading.Timer(self.busy_until - now, self._echo, (target, token))
                t.daemon = True
                self.timers.append(t)
                t.start()

    def _echo(self, addr, token):
        try:
            self.sock.sendto((token + "\r\n").encode(), addr)
        except OSError:
            pass

    def serve(self, port, host="127.0.0.1"):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))

        def loop():
            while True:
                try:
                    data, addr = self.sock.recvfrom(2000)
                except OSError:
                    return
                self.handle(data, addr)
        threading.Thread(target=loop, daemon=True).start()
        return self


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=6789)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    d = FakeCwDaemon().serve(a.port, a.host)
    print(f"fake cwdaemon listening on {a.host}:{a.port} (Ctrl-C to stop)")
    seen = 0
    try:
        while True:
            time.sleep(0.2)
            while seen < len(d.log):
                print("  ", d.log[seen])
                seen += 1
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
