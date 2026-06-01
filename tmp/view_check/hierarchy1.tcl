set __copilot_out_243c9109ab114da1a1b74915d6b19abf [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/hierarchy1.txt" w]
set __copilot_remote_result_path "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/hierarchy1.txt.remote"
catch {file delete -force $__copilot_remote_result_path}
set rc [catch {
    package require dde
    dde execute TclEval CarMaker {
        set __copilot_remote_out [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/hierarchy1.txt.remote" w]
        set __copilot_remote_rc [catch {
            set ch [winfo children .]
            format "root_children=%s" $ch
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
    puts -nonewline $__copilot_out_243c9109ab114da1a1b74915d6b19abf $__copilot_remote_payload
    catch {file delete -force $__copilot_remote_result_path}
} else {
    puts $__copilot_out_243c9109ab114da1a1b74915d6b19abf "rc=$rc"
    puts $__copilot_out_243c9109ab114da1a1b74915d6b19abf "msg_begin"
    puts $__copilot_out_243c9109ab114da1a1b74915d6b19abf $msg
    puts $__copilot_out_243c9109ab114da1a1b74915d6b19abf "msg_end"
}
close $__copilot_out_243c9109ab114da1a1b74915d6b19abf
