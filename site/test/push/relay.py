"""FCM_PROXY_CMD for the stand-in push service: stdin/stdout <-> TCP 127.0.0.1:PORT.  Unlike nc, it passes the far
end closing the connection on to the server (it exits), as ssh -W does on the real server.  Usage: relay.py PORT"""
import os, socket, sys, threading
s = socket.create_connection(("127.0.0.1", int(sys.argv[1])))


def up():
    while True:
        b = os.read(0, 65536)
        if not b:
            s.shutdown(socket.SHUT_WR)
            return
        s.sendall(b)


threading.Thread(target=up, daemon=True).start()
while True:
    b = s.recv(65536)
    if not b:
        os._exit(0)
    os.write(1, b)
