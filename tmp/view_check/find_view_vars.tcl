set __copilot_out_d9b3969e523748e39b13a6bc89fa4a39 [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/find_view_vars.txt" w]
set __copilot_remote_result_path "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/find_view_vars.txt.remote"
catch {file delete -force $__copilot_remote_result_path}
set rc [catch {
    package require dde
    dde execute TclEval CarMaker {
        set __copilot_remote_out [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/find_view_vars.txt.remote" w]
        set __copilot_remote_rc [catch {
            set all [array names View]
            set trimmed [lrange $all 0 19]
            format "total=%d first20=%s" [llength $all] $trimmed
        } __copilot_remote_msg]
        puts $__copilot_remote_out "rc=$__copilot_remote_rc"
        puts $__copilot_remote_out "msg_begin"
        puts $__copilot_remote_out $__copilot_remote_msg
        puts $__copilot_remote_out "msg_end"
        close $__copilot_remote_out
        if {$__copilot_remote_rc != 0} {error $__copilot_remote_msg}
    }
} msg]
if {[file exists $__copilot_remote_result_path]} {
    set __copilot_remote_in [open $__copilot_remote_result_path r]
    set __copilot_remote_payload [read $__copilot_remote_in]
    close $__copilot_remote_in
    puts -nonewline $__copilot_out_d9b3969e523748e39b13a6bc89fa4a39 $__copilot_remote_payload
    catch {file delete -force $__copilot_remote_result_path}
} else {
    puts $__copilot_out_d9b3969e523748e39b13a6bc89fa4a39 "rc=$rc"
    puts $__copilot_out_d9b3969e523748e39b13a6bc89fa4a39 "msg_begin"
    puts $__copilot_out_d9b3969e523748e39b13a6bc89fa4a39 $msg
    puts $__copilot_out_d9b3969e523748e39b13a6bc89fa4a39 "msg_end"
}
close $__copilot_out_d9b3969e523748e39b13a6bc89fa4a39
