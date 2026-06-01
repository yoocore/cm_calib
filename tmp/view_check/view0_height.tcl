set __copilot_out_8ddf597909684f1380f5445739a5413d [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/view0_height.txt" w]
set __copilot_remote_result_path "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/view0_height.txt.remote"
catch {file delete -force $__copilot_remote_result_path}
set rc [catch {
    package require dde
    dde execute TclEval CarMaker {
        set __copilot_remote_out [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/view0_height.txt.remote" w]
        set __copilot_remote_rc [catch {
            format "view0_height=%s" [catch {catch {winfo height .view0}} val];format "view0_height=%s" $val
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
    puts -nonewline $__copilot_out_8ddf597909684f1380f5445739a5413d $__copilot_remote_payload
    catch {file delete -force $__copilot_remote_result_path}
} else {
    puts $__copilot_out_8ddf597909684f1380f5445739a5413d "rc=$rc"
    puts $__copilot_out_8ddf597909684f1380f5445739a5413d "msg_begin"
    puts $__copilot_out_8ddf597909684f1380f5445739a5413d $msg
    puts $__copilot_out_8ddf597909684f1380f5445739a5413d "msg_end"
}
close $__copilot_out_8ddf597909684f1380f5445739a5413d
