import Toybox.Lang;
import Toybox.WatchUi;

// BehaviorDelegate rather than InputDelegate, so the same three gestures work
// whether or not this watch gives Connect IQ its touchscreen -- START and a tap
// both arrive as onSelect, DOWN and a swipe both arrive as onNextPage. Which of
// those is true on the FR165 is itself one of the things to find out.
class SpikeDelegate extends WatchUi.BehaviorDelegate {

    function initialize() {
        BehaviorDelegate.initialize();
    }

    // START / tap -- the safe one. Reads, writes nothing.
    function onSelect() as Boolean {
        $.probe.gate();
        return true;
    }

    // DOWN -- the real write. Puts a row in the database.
    function onNextPage() as Boolean {
        $.probe.logVoid();
        return true;
    }

    // UP -- clear the screen. The log file keeps everything regardless.
    function onPreviousPage() as Boolean {
        $.probe.lines = [];
        WatchUi.requestUpdate();
        return true;
    }
}
