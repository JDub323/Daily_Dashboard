import Toybox.Graphics;
import Toybox.Lang;
import Toybox.WatchUi;

// Five lines of monospaced verdict on a black ground. Black because this is an
// AMOLED and the app is opened in a dark bathroom, and because the one thing
// this screen must do is be legible at arm's length in one glance.
class SpikeView extends WatchUi.View {

    function initialize() {
        View.initialize();
    }

    function onUpdate(dc as Dc) as Void {
        dc.setColor(Graphics.COLOR_BLACK, Graphics.COLOR_BLACK);
        dc.clear();

        var w = dc.getWidth();
        var h = dc.getHeight();

        dc.setColor(Graphics.COLOR_ORANGE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, h * 0.14, Graphics.FONT_XTINY, "IN-OUT PROBE",
                    Graphics.TEXT_JUSTIFY_CENTER);

        var lines = $.probe.lines;
        var shown = 5;
        var first = lines.size() - shown;
        if (first < 0) { first = 0; }

        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        var y = h * 0.30;
        for (var i = first; i < lines.size(); i += 1) {
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, lines[i],
                        Graphics.TEXT_JUSTIFY_CENTER);
            y += h * 0.09;
        }

        if (lines.size() == 0) {
            dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, h * 0.44, Graphics.FONT_XTINY, "START = read",
                        Graphics.TEXT_JUSTIFY_CENTER);
            dc.drawText(w / 2, h * 0.53, Graphics.FONT_XTINY, "DOWN  = write a row",
                        Graphics.TEXT_JUSTIFY_CENTER);
        }

        // The bottom third is deliberately empty. It is where a wrist sits when
        // the arm is down, and on a round screen it is the least readable band.
    }
}
