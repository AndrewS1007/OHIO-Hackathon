"""Text the assistant from your terminal, no Twilio needed.

    python scripts/sms_sim.py              # you are Maria (+15550100001)
    python scripts/sms_sim.py +15559998888 # a new number: text DEMO or LINK <premise>

Extra commands (not SMS): /advance <hours>   move the storm clock
                          /whatif crews=1 extra_poles=1 access=1.5
                          /reset
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from concierge import service, sms  # noqa: E402


def show(msgs):
    for phone, items in msgs.items():
        for m in items:
            if m["kind"] == "escalation":
                print(f"  [{m.get('at', '')}] ** {m['action']} ({m['rule']}): {m['why']}")
            else:
                print(f"  [{m.get('at', '')}] -> {phone}: {m['text']}")


def main():
    phone = sys.argv[1] if len(sys.argv) > 1 else "+15550100001"
    print(f"Texting as {phone}. Ctrl-D to quit. {sms.MENU}")
    while True:
        try:
            body = input("you> ").strip()
        except EOFError:
            break
        if body.startswith("/advance"):
            show(service.advance(float(body.split()[1])))
            continue
        if body.startswith("/whatif"):
            for kv in body.split()[1:]:
                k, v = kv.split("=")
                service.WORLD.what_if[k] = float(v) if k == "access" else int(v)
            show({p: service.proactive(s) for p, s in service.SESSIONS.items()})
            continue
        if body == "/reset":
            service.reset_world()
            continue
        r = sms.handle(phone, body)
        print(f"bot> {r['text']}   ({r['segments']} SMS)")


if __name__ == "__main__":
    main()
