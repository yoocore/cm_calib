set __copilot_out_09924afeb5ab4763a71c7b9ebde9743e [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/view0_width.txt" w]
set __copilot_remote_result_path "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/view0_width.txt.remote"
catch {file delete -force $__copilot_remote_result_path}
set rc [catch {
    package require dde
    dde execute TclEval CarMaker {
        set __copilot_remote_out [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/view0_width.txt.remote" w]
        set __copilot_remote_rc [catch {
            format "view0_width=%s" [catch {catch {winfo width .view0}} val];format "view0_width=%s" $val
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
    puts -nonewline $__copilot_out_09924afeb5ab4763a71c7b9ebde9743e $__copilot_remote_payload
    catch {file delete -force $__copilot_remote_result_path}
} else {
    puts $__copilot_out_09924afeb5ab4763a71c7b9ebde9743e "rc=$rc"
    puts $__copilot_out_09924afeb5ab4763a71c7b9ebde9743e "msg_begin"
    puts $__copilot_out_09924afeb5ab4763a71c7b9ebde9743e $msg
    puts $__copilot_out_09924afeb5ab4763a71c7b9ebde9743e "msg_end"
}
close $__copilot_out_09924afeb5ab4763a71c7b9ebde9743e
