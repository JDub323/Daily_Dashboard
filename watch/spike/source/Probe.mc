//
// The one experiment, and the record it leaves behind.
//
// Every result is written twice: onto the screen, where you can read it while
// standing in a bathroom, and through System.println, which lands in
//
//     \GARMIN\APPS\LOGS\INOUT.TXT
//
// on the watch's own filesystem if that file exists before the app runs. The
// file is why this app is worth sideloading rather than simulating: the run
// that matters is the one with the phone two rooms away, and that is exactly
// the run you cannot watch. Wear it, provoke it, plug the watch back in, and
// the answer is sitting in a text file.
//
import Toybox.Communications;
import Toybox.Lang;
import Toybox.System;
import Toybox.Time;
import Toybox.Time.Gregorian;
import Toybox.WatchUi;

// Read off `tailscale serve status` on 2026-09-17. Compiled in rather than
// configured, because Garmin's own documentation says app settings depend on
// the store to function and this app will never be in the store. Changing it
// is a rebuild.
const BASE = "https://your-laptop.your-tailnet.ts.net";

// The smallest 200 the dashboard has: no query parameters, ~112 bytes of JSON,
// and it touches nothing. Response size is not an idle concern -- a body too
// large for the BLE relay comes back as NETWORK_RESPONSE_OUT_OF_MEMORY, and a
// spike that fails that way would look exactly like a spike that could not
// reach the server at all. Small on purpose, so a failure means what it says.
const GATE_PATH = "/api/portfolio/status";

const BATHROOM_PATH = "/api/bathroom";

class Probe {

    // The last few results, newest last. Bounded when drawn, not when stored.
    public var lines as Array<String> = [];

    // One request at a time. The relay answers BLE_QUEUE_FULL to a burst, and
    // a spike that provokes it learns nothing except that it provoked it.
    public var busy as Boolean = false;

    function initialize() {
    }

    // -- the clock ---------------------------------------------------------
    //
    // FORMAT_SHORT, not FORMAT_MEDIUM: short gives month as a number and
    // medium gives it as "Sep", and the server wants an ISO date.
    //
    // This is [Invariant 1] holding on a third client. The body carries the
    // watch's own date and hour, the server never consults its own clock, and
    // a void logged at 09:00 and delivered at 21:00 still lands at 09:00.

    function today() as String {
        var i = Gregorian.info(Time.now(), Time.FORMAT_SHORT);
        return i.year.format("%04d") + "-" + i.month.format("%02d") + "-" + i.day.format("%02d");
    }

    function hourNow() as Number {
        return Gregorian.info(Time.now(), Time.FORMAT_SHORT).hour;
    }

    function stamp() as String {
        var i = Gregorian.info(Time.now(), Time.FORMAT_SHORT);
        return i.hour.format("%02d") + ":" + i.min.format("%02d");
    }

    // -- the record --------------------------------------------------------

    function note(text as String) as Void {
        var line = stamp() + " " + text;
        System.println(line);
        lines.add(line);
        WatchUi.requestUpdate();
    }

    // Whether the phone is there at all, recorded next to every result.
    // Without it a -104 and a routing failure read the same on paper, and they
    // are completely different verdicts.
    function phone() as String {
        return System.getDeviceSettings().phoneConnected ? "ph+" : "ph-";
    }

    // -- the gate ----------------------------------------------------------

    function gate() as Void {
        if (busy) { return; }
        busy = true;
        note("GET " + phone());
        Communications.makeWebRequest(
            BASE + GATE_PATH,
            null,
            {
                :method => Communications.HTTP_REQUEST_METHOD_GET,
                :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON
            },
            method(:onGate)
        );
    }

    function onGate(code as Number, data as Dictionary or String or Null) as Void {
        busy = false;
        note("GET " + code + " " + explain(code));
    }

    // -- the real write ----------------------------------------------------
    //
    // This posts an actual row into the actual database. It is the second half
    // of the proof -- a GET only shows that bytes travel, and the thing worth
    // knowing is whether Monkey C's JSON encoder produces a body the server's
    // validator accepts. A 422 here is a good outcome wearing a bad hat: the
    // server received it and named the field it disliked.
    //
    // Delete the row afterwards on /m. One tap.

    function logVoid() as Void {
        if (busy) { return; }
        busy = true;
        note("PUT " + phone());
        var body = {
            "date" => today(),
            "hour" => hourNow(),
            "kind" => "urine",
            "volume" => "medium",
            "color" => "light"
        };
        Communications.makeWebRequest(
            BASE + BATHROOM_PATH,
            body,
            {
                :method => Communications.HTTP_REQUEST_METHOD_POST,
                :headers => { "Content-Type" => Communications.REQUEST_CONTENT_TYPE_JSON },
                :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON
            },
            method(:onPost)
        );
    }

    function onPost(code as Number, data as Dictionary or String or Null) as Void {
        busy = false;
        // The endpoint answers with the whole health panel. If that body is
        // too big to come back over BLE this is where it shows, and the
        // distinction matters: the row was still written.
        var size = (data == null) ? "nil" : "ok";
        note("PUT " + code + " " + explain(code) + " " + size);
    }

    // -- the decoder -------------------------------------------------------
    //
    // Named here so the log file reads as a verdict rather than as a number
    // you have to go and look up three weeks later.

    function explain(code as Number) as String {
        if (code == 200) { return "PASS"; }
        if (code == 422) { return "validator"; }   // reached the server
        if (code == 404) { return "path"; }        // reached the server
        if (code == 500) { return "server"; }      // reached the server
        if (code == -104) { return "no-phone"; }
        if (code == -2) { return "ble-timeout"; }
        if (code == -300) { return "NO-ROUTE"; }   // the verdict that kills it
        if (code == -400) { return "body-nonjson"; }
        if (code == -402) { return "too-big"; }
        if (code == -403) { return "refused"; }
        return "?";
    }
}
