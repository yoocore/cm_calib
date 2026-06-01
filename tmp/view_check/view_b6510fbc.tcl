set out [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/view_b6510fbc.txt" w]
set rc [catch {
    package require dde
    dde execute TclEval CarMaker {
        set out2 [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/view_b6510fbc.txt.remote" w]
        set rc2 [catch {
            scan $View(ev.view) %d vno
            set wpath .view$vno
            set wi [$wpath.gl0 cget -width]
            set he [$wpath.gl0 cget -height]
            set vgeom [winfo geometry $wpath]
            set mgeom [wm geometry .]
            format "view_no=%d;gl_w=%d;gl_h=%d;view_geom=%s;movie_geom=%s" $vno $wi $he $vgeom $mgeom
        } msg2]
        puts $out2 "rc=$rc2"
        puts $out2 "msg_begin"
        puts $out2 $msg2
        puts $out2 "msg_end"
        close $out2
    }
} msg]
puts $out "rc=$rc"
puts $out "msg_begin"
puts $out $msg
puts $out "msg_end"
close $out
