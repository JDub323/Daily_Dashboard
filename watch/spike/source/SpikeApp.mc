import Toybox.Application;
import Toybox.Lang;
import Toybox.System;
import Toybox.WatchUi;

// The probe outlives every view, because a response can arrive after you have
// stopped looking at the screen that asked for it.
var probe as Probe or Null = null;

class SpikeApp extends Application.AppBase {

    function initialize() {
        AppBase.initialize();
    }

    function onStart(state as Dictionary or Null) as Void {
        $.probe = new Probe();
        // Stamped so a log file covering several days can be cut into runs.
        System.println("--- probe start " + $.probe.today() + " " + $.probe.stamp() + " ---");
    }

    function onStop(state as Dictionary or Null) as Void {
    }

    function getInitialView() as Array<Views or InputDelegates> {
        return [ new SpikeView(), new SpikeDelegate() ] as Array<Views or InputDelegates>;
    }
}
