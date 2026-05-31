"""
DH-P2P + PTCP Implementation
"""
import argparse
import datetime
import random
import select
import socket
import subprocess
import sys
import time
from urllib.parse import quote

from helpers import (
    MAIN_PORT,
    MAIN_SERVER,
    P2P_SERVERS,
    UDP,
    PTCPPayload,
    get_auth,
    get_dec,
    get_enc,
    get_key,
    get_nonce,
)


HEARTBEAT_BODY = b"\x13\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"


def ptcp_body_label(body):
    if not body:
        return "empty"
    return {
        0x00: "sync",
        0x10: "payload",
        0x11: "bind",
        0x12: "status",
        0x13: "heartbeat",
    }.get(body[0], f"command-0x{body[0]:02x}")


def ptcp_status_text(body):
    if len(body) >= 12 and body[0] == 0x12:
        return body[12:].decode("utf-8", errors="replace")
    return ""


def ptcp_payload_preview(body):
    if len(body) <= 12 or body[0] != 0x10:
        return ""
    payload = body[12:200]
    if not payload:
        return ""
    first_line = payload.split(b"\r\n", 1)[0].decode("utf-8", errors="replace")
    if all((ch.isprintable() or ch.isspace()) for ch in first_line):
        return first_line[:160]
    return ""


def maybe_send_heartbeat(remote, last_heartbeat):
    now = time.time()
    if now - last_heartbeat >= 5:
        remote.request_ptcp(HEARTBEAT_BODY)
        return now
    return last_heartbeat


def read_bind_status(remote, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = max(0.2, min(5, deadline - time.time()))
        res = remote.read_ptcp(timeout=remaining)
        print(f"PTCP bind/read: {ptcp_body_label(res.body)} len={len(res.body)}", flush=True)
        if len(res.body) == 0:
            continue

        remote.request_ptcp()
        if res.body[0] != 0x12:
            continue

        status = ptcp_status_text(res.body)
        print(f"PTCP bind status: {status}", flush=True)
        return status

    raise TimeoutError("Timed out waiting for PTCP bind status")


def main(
    serial,
    dtype=0,
    username=None,
    password=None,
    debug=False,
    listen_port=8554,
    remote_port=554,
    p2p_server="easy4ip",
    relay=False,
):
    socketserver = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    socketserver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    socketserver.bind(("0.0.0.0", listen_port))
    socketserver.listen(5)
    print(f"Listening on port {listen_port}", flush=True)

    if debug:
        subprocess.Popen(
            [
                "ffplay",
                "-rtsp_transport",
                "tcp",
                "-i",
                f"rtsp://{username}:{quote(password)}@127.0.0.1/cam/realmonitor?channel=6&subtype=0",
            ]
        )

    cloud_host, cloud_port = P2P_SERVERS.get(p2p_server, (MAIN_SERVER, MAIN_PORT))
    print(f"P2P cloud: {cloud_host}:{cloud_port}", flush=True)
    main_remote = UDP(cloud_host, cloud_port, debug)
    res = main_remote.request("/probe/p2psrv")

    res = main_remote.request(f"/online/p2psrv/{serial}")

    p2psrv_server, p2psrv_port = res["data"]["body"]["US"].split(":")
    p2psrv_port = int(p2psrv_port)

    p2psrv_remote = UDP(p2psrv_server, p2psrv_port, debug)
    res = p2psrv_remote.request(f"/probe/device/{serial}")
    res = p2psrv_remote.request(f"/info/device/{serial}")

    res = main_remote.request("/online/relay")
    relay_server, relay_port = res["data"]["body"]["Address"].split(":")
    relay_port = int(relay_port)

    # p2p-channel must use the same UDP socket as /online/p2psrv (see dh-p2p Rust impl).
    laddr = f"127.0.0.1:{main_remote.lport}"
    ipaddr = f"<IpEncrpt>true</IpEncrpt><LocalAddr>{laddr}</LocalAddr>"
    auth = ""
    aid = random.randbytes(8)

    if dtype > 0:
        key = get_key(username, password)
        nonce = get_nonce()

        laddr = get_enc(key, nonce, laddr)
        ipaddr = f"<IpEncrptV2>true</IpEncrptV2><LocalAddr>{laddr}</LocalAddr>"
        auth = "" if dtype == 0 else get_auth(username, key, nonce, laddr)

    main_remote.request(
        f"/device/{serial}/p2p-channel",
        f"<body>{auth}<Identify>{' '.join(f'{b:x}' for b in aid)}</Identify>{ipaddr}<version>5.0.0</version></body>",
        should_read=False,
    )

    # Keep the same UDP socket that probed the device, matching the Rust dh-p2p flow.
    relay_remote = p2psrv_remote
    relay_remote.rhost = relay_server
    relay_remote.rport = relay_port
    res = relay_remote.request("/relay/agent")
    token = res["data"]["body"]["Token"]
    agent_server, agent_port = res["data"]["body"]["Agent"].split(":")
    agent_port = int(agent_port)

    relay_remote.rhost = agent_server
    relay_remote.rport = agent_port
    relay_remote.request(
        f"/relay/start/{token}",
        "<body><Client>:0</Client></body>",
    )

    print("Waiting for p2p-channel response...", flush=True)
    res = main_remote.read(return_error=True, timeout=120)
    if res["code"] == 100:
        res = main_remote.read(return_error=True, timeout=120)
    if res["code"] < 200:
        res = main_remote.read(return_error=True, timeout=120)

    if res["code"] >= 400:
        print("Error:", res["status"])

        if dtype == 0 and res["code"] == 403:
            print("Device requires authentication when creating P2P channel.")
            print("Try again with:")
            print(
                f"main.py --type 1 --username <username> --password <password> {serial}"
            )

        sys.exit(1)

    device_laddr = res["data"]["body"]["LocalAddr"]
    if dtype > 0:
        nonce = res["data"]["body"]["Nonce"]
        device_laddr = get_dec(key, nonce, device_laddr)

    device_server, device_port = res["data"]["body"]["PubAddr"].split(":")
    device_port = int(device_port)
    device_remote = main_remote
    device_remote.rhost = device_server
    device_remote.rport = device_port

    if dtype > 0:
        auth = get_auth(username, key, nonce)

    relay_session = relay_remote
    relay_session.rhost = cloud_host
    relay_session.rport = cloud_port
    relay_body = f"<body><agentAddr>{agent_server}:{agent_port}</agentAddr></body>"
    if dtype > 0 and auth:
        relay_body = f"<body>{auth}<agentAddr>{agent_server}:{agent_port}</agentAddr></body>"
    got_agent_ack = False
    for relay_attempt in range(1, 4):
        relay_session.rhost = cloud_host
        relay_session.rport = cloud_port
        relay_session.request(
            f"/device/{serial}/relay-channel",
            relay_body,
            should_read=False,
        )

        relay_session.rhost = agent_server
        relay_session.rport = agent_port
        try:
            relay_session.read(timeout=5)
            got_agent_ack = True
            break
        except (TimeoutError, socket.timeout):
            print(f"Agent HTTP ack timeout ({relay_attempt}/3)", flush=True)
            time.sleep(0.5)
    if not got_agent_ack:
        print("Agent HTTP ack: timeout (continuing PTCP)", flush=True)

    res = None
    for sync_attempt in range(1, 4):
        relay_session.request_ptcp(b"\x00\x03\x01\x00")
        try:
            res = relay_session.read_ptcp(timeout=10)
            break
        except (TimeoutError, socket.timeout):
            print(f"PTCP sync timeout ({sync_attempt}/3)", flush=True)
    if res is None:
        print("PTCP sync timeout. Relay channel did not become ready.", flush=True)
        sys.exit(1)

    if relay:
        device_remote = relay_session
        print("Ready to connect", flush=True)
        print(
            f"Test with: rtsp://127.0.0.1:{listen_port}/cam/realmonitor?channel=1&subtype=0",
            flush=True,
        )
        goto_ready_loop = True
    else:
        goto_ready_loop = False

    if not goto_ready_loop:
        relay_session.request_ptcp(b"\x17\x00\x00\x00" + b"\x00\x00\x00\x00\x00\x00\x00\x00")
        res = relay_session.read_ptcp(timeout=30)
        while len(res.body) == 0:
            res = relay_session.read_ptcp(timeout=30)
        sign = res.body[12:]

        relay_session.request_ptcp()

        device_remote.rhost = device_server
        device_remote.rport = device_port
        for attr in ("ptcp_sent", "ptcp_recv", "ptcp_count", "ptcp_id", "rmid"):
            setattr(device_remote, attr, getattr(relay_session, attr))

        aid = bytes(0xFF - b for b in aid)
        cookie = random.randbytes(4)
        trasn_id = random.randbytes(12)
        eaddr = device_port.to_bytes(2) + socket.inet_aton(device_server)
        eaddr = bytes(0xFF - b for b in eaddr)

        data = (
            b"\xff\xfe\xff\xe7"
            + cookie
            + trasn_id
            + b"\x7f\xd5\xff\xf7"
            + aid
            + b"\xff\xfb\xff\xf7\xff\xfe"
            + eaddr
        )
        print(f":{device_remote.lport} >>> {device_remote.rhost}:{device_remote.rport}")
        print("".join(f"\\x{b:02X}" for b in data))
        device_remote.send(data)

        try:
            data = device_remote.recv(timeout=5)
        except socket.timeout:
            print("Timeout occurred while waiting for a response from the device.")
            print("If the issue persists, you may need to use relay mode with this device.")
            sys.exit(1)

        print("Data <<<")
        print("".join(f"\\x{b:02X}" for b in data))

        rtrans_id = data[8:20]
        ip, port = device_laddr.split(":")
        port = int(port)
        eaddr = port.to_bytes(2) + socket.inet_aton(ip)

        data = (
            b"\xfe\xfe\xff\xe7"
            + cookie
            + rtrans_id
            + b"\x7f\xd6\xff\xf7"
            + aid
            + b"\xff\xfb\xff\xf7\xff\xfe"
            + eaddr
        )
        print("Request >>>")
        print("".join(f"\\x{b:02X}" for b in data))
        device_remote.send(data)

        if dtype > 0:
            data = device_remote.recv()
            print("Data <<<")
            print("".join(f"\\x{b:02X}" for b in data))

            data = (
                b"\xfe\xfe\xff\xf3"
                + cookie
                + rtrans_id
                + b"\x7f\xd6\xff\xf7"
                + aid
                + b"\xff\xfb\xff\xf7\xff\xfe"
                + b"\xa8\x13\x3f\x57\xfe\x37"
            )

            for _ in range(5):
                print("Request >>>")
                print("".join(f"\\x{b:02X}" for b in data))
                device_remote.send(data)

        for _ in range(5):
            data = device_remote.recv(timeout=5)
            print("Data <<<")
            print("".join(f"\\x{b:02X}" for b in data))

        device_remote.request_ptcp(b"\x00\x03\x01\x00")
        res = device_remote.read_ptcp()
        assert res.body == b"\x00\x03\x01\x00"

        device_remote.request_ptcp(
            b"\x19\x00\x00\x00" + b"\x00\x00\x00\x00" + b"\x00\x00\x00\x00" + sign
        )
        res = device_remote.read_ptcp()
        if len(res.body) == 0:
            res = device_remote.read_ptcp()
        assert res.body[0] == 0x1A

        device_remote.request_ptcp(
            b"\x1b\x00\x00\x00" + b"\x00\x00\x00\x00" + b"\x00\x00\x00\x00"
        )
        res = device_remote.read_ptcp()
        assert len(res.body) == 0

        print("Ready to connect", flush=True)
        print(
            f"Test with: rtsp://127.0.0.1:{listen_port}/cam/realmonitor?channel=1&subtype=0",
            flush=True,
        )
    last_heartbeat = 0.0
    while True:
        ready, _, _ = select.select([socketserver], [], [], 0.1)

        if not ready:
            ptcp_ready, _, _ = select.select([device_remote], [], [], 0)

            if not ptcp_ready:
                last_heartbeat = maybe_send_heartbeat(device_remote, last_heartbeat)
                continue

            # only simplex, duplex is not supported
            res = device_remote.read_ptcp()
            print(f"PTCP idle: {ptcp_body_label(res.body)} len={len(res.body)}", flush=True)
            if len(res.body) == 0:
                continue

            device_remote.request_ptcp()

            continue

        socketclient, address = socketserver.accept()
        print(f"Connection from {address}")

        realm_id = random.randint(0x00000000, 0xFFFFFFFF)
        device_remote.request_ptcp(
            b"\x11\x00\x00\x00"
            + realm_id.to_bytes(4, "big")
            + b"\x00\x00\x00\x00"
            + remote_port.to_bytes(4, "big")
            + b"\x7f\x00\x00\x01",
        )
        try:
            bind_status = read_bind_status(device_remote, timeout=30)
        except (TimeoutError, socket.timeout):
            print("PTCP bind status timeout", flush=True)
            socketclient.close()
            continue
        if bind_status != "CONN":
            print(f"PTCP bind rejected: {bind_status}", flush=True)
            socketclient.close()
            continue

        remote_disconnected = False
        try:
            while True:
                disconnected = False
                last_heartbeat = maybe_send_heartbeat(device_remote, last_heartbeat)
                ptcp_ready, _, _ = select.select([device_remote], [], [], 0.1)

                # if ptcp_ready:
                while ptcp_ready:
                    res = device_remote.read_ptcp()
                    print(f"PTCP rx: {ptcp_body_label(res.body)} len={len(res.body)}", flush=True)

                    if len(res.body) == 0:
                        continue

                    device_remote.request_ptcp()

                    if res.body[0] == 0x12:
                        status = ptcp_status_text(res.body)
                        print(f"PTCP status: {status}", flush=True)
                        if status == "DISC":
                            remote_disconnected = True
                            disconnected = True
                            break
                        continue

                    if res.body[0] != 0x10:
                        continue

                    preview = ptcp_payload_preview(res.body)
                    if preview:
                        print(f"RTSP rx: {preview}", flush=True)

                    body = PTCPPayload.parse(res.body)

                    if debug:
                        print()
                        print(body)
                        print(f"[{datetime.datetime.now().isoformat()}]")
                        print("Data <<<")
                        print(body.payload)
                        print()

                    socketclient.send(body.payload)

                    ptcp_ready, _, _ = select.select([device_remote], [], [], 0.1)

                if disconnected:
                    break

                client_ready, _, _ = select.select([socketclient], [], [], 0)

                if not client_ready:
                    continue

                data = socketclient.recv(4096)

                if not data:
                    print("Connection closed?")
                    break
                print(f"TCP client bytes: {len(data)}", flush=True)

                if debug:
                    print()
                    print(f"[{datetime.datetime.now().isoformat()}]")
                    print("Data >>>")
                    print(data)
                    print()

                device_remote.request_ptcp(bytes(PTCPPayload(realm_id, data)))

        # handle connection reset by peer
        except ConnectionResetError:
            print("Connection reset by peer")
        except BrokenPipeError:
            print("Broken pipe")
        finally:
            print("Cleaning up connection")
            if not remote_disconnected:
                try:
                    device_remote.request_ptcp(
                        b"\x12\x00\x00\x00"
                        + realm_id.to_bytes(4, "big")
                        + b"\x00\x00\x00\x00"
                        + b"DISC"
                    )

                    res = device_remote.read_ptcp(timeout=5)

                    while len(res.body) == 0 or res.body[0] == 0x10:
                        if len(res.body) > 0:
                            device_remote.request_ptcp()

                        res = device_remote.read_ptcp(timeout=5)

                    if res.body[0] == 0x12:
                        device_remote.request_ptcp()
                except (TimeoutError, socket.timeout):
                    print("PTCP disconnect ack timeout", flush=True)

            socketclient.close()
            print("Connection closed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("serial", help="Serial number of the camera")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("-t", "--type", type=int, help="Type of the camera", default=0)
    parser.add_argument("-u", "--username", help="Username of the camera")
    parser.add_argument("-p", "--password", help="Password of the camera")
    parser.add_argument(
        "--listen-port",
        type=int,
        default=8554,
        help="Local TCP port for RTSP clients (default 8554; avoid 554 conflicts)",
    )
    parser.add_argument(
        "--remote-port",
        type=int,
        default=554,
        help="Remote camera TCP port to bind through P2P (default 554 for RTSP)",
    )
    parser.add_argument(
        "--p2p-server",
        choices=sorted(P2P_SERVERS.keys()),
        default="easy4ip",
        help="P2P cloud (default easy4ip)",
    )
    parser.add_argument("--relay", action="store_true", help="Use relay channel instead of direct UDP hole punch")
    args = parser.parse_args()

    if args.username is None or args.password is None:
        if args.type > 0:
            parser.error("Username and password are required for type > 0")
        elif args.debug:
            parser.error("Username and password are required in debug mode")

    if args.serial:
        main(
            serial=args.serial,
            dtype=args.type,
            username=args.username,
            password=args.password,
            debug=args.debug,
            listen_port=args.listen_port,
            remote_port=args.remote_port,
            p2p_server=args.p2p_server,
            relay=args.relay,
        )
