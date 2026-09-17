# In-Out Probe

A throwaway Connect IQ watch app whose only job is to answer the question that
decides whether a wrist client is possible at all:

> Will Garmin Connect, relaying for the watch over BLE, resolve and route
> `https://your-laptop.your-tailnet.ts.net` to a 100.x address?

A Connect IQ app has no network stack. Every `makeWebRequest` is handed to the
phone, which performs it on the phone's network. Nobody has written down whether
that works over Tailscale — no forum post, no repo. So this gets built before
anything else does.

## What it does

Three gestures, and every result is written both to the screen and to a log file
on the watch:

| | |
|---|---|
| **START** or tap | GET `/api/portfolio/status` — reads, writes nothing |
| **DOWN** | POST `/api/bathroom` — **writes a real row**, delete it on `/m` |
| **UP** | clear the screen (the log file keeps everything) |

Each line reads `HH:MM GET 200 PASS` or `HH:MM GET -300 NO-ROUTE`, with `ph+` /
`ph-` recording whether the phone was connected when the request went out. That
last part matters: without it a `-104` and a routing failure read identically
afterwards, and they are opposite verdicts.

## Build

```sh
./build.sh
```

Needs the `fr165` device configuration, which only the SDK Manager can fetch
(the device endpoint is behind a Garmin account sign-in). Without it the build
stops at `ERROR: Invalid device id specified: 'fr165'`.

## Sideload

With the watch on USB, copy `inout.prg` to `\GARMIN\APPS\`. Then — and this is
the part worth not skipping — create the directory `\GARMIN\APPS\LOGS\` and an
empty file `INOUT.TXT` inside it. If that file exists, every `System.println`
from the app is appended to it.

That is what makes the interesting run observable. The run that decides this
project is the one with the phone in another room, which is exactly the run you
cannot stand and watch. Provoke it on the wrist, plug the watch back in, read
the file.

## Reading the result

| Code | Meaning | Verdict |
|---|---|---|
| `200 PASS` | it worked | green light |
| `422 validator` | server got the body and named a bad field | connectivity proven |
| `404 path` / `500 server` | reached the server | connectivity proven |
| `-104 no-phone` | phone not connected, or Garmin Connect not running | not a verdict — retry |
| `-2 ble-timeout` | the relay hung | retry |
| `-300 NO-ROUTE` | the phone could not resolve or reach the name | **the Tailscale path is dead** |
| `-402 too-big` | response too large for the relay | shrink the response |
| `-403 refused` | rejected before it left | cert or scheme |

A `422` on the POST is a good outcome wearing a bad hat: it means Monkey C's
JSON encoder produced a body the server read and disagreed with, which is a
much smaller problem than not arriving.

## Notes

The base URL is compiled in. Garmin's own documentation says app settings depend
on the Connect IQ store to function, and this app will never be in the store, so
a settings screen is not a thing to design around. Changing the URL is a rebuild.
