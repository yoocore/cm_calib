set out [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/test_6677a4ad.txt" w]
set rc [catch {
    package require dde
    dde execute TclEval CarMaker {
        set out2 [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/test_6677a4ad.txt.remote" w]
        set rc2 [catch { winfo exists . } msg2]
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
