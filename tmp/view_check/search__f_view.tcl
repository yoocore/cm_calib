set __copilot_out_87b3a3a8f7bb4e1e839ca8047c5ad74e [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/search__f_view.txt" w]
set __copilot_remote_result_path "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/search__f_view.txt.remote"
catch {file delete -force $__copilot_remote_result_path}
set rc [catch {
    package require dde
    dde execute TclEval CarMaker {
        set __copilot_remote_out [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/search__f_view.txt.remote" w]
        set __copilot_remote_rc [catch {
            catch {set w [winfo width .f.view]} we
            catch {set h [winfo height .f.view]} he
            catch {set ch2 [winfo children .f.view]} ch2e
            format "pattern=.f.view w=%s h=%s children=%s" $we $he $ch2e
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
    puts -nonewline $__copilot_out_87b3a3a8f7bb4e1e839ca8047c5ad74e $__copilot_remote_payload
    catch {file delete -force $__copilot_remote_result_path}
} else {
    puts $__copilot_out_87b3a3a8f7bb4e1e839ca8047c5ad74e "rc=$rc"
    puts $__copilot_out_87b3a3a8f7bb4e1e839ca8047c5ad74e "msg_begin"
    puts $__copilot_out_87b3a3a8f7bb4e1e839ca8047c5ad74e $msg
    puts $__copilot_out_87b3a3a8f7bb4e1e839ca8047c5ad74e "msg_end"
}
close $__copilot_out_87b3a3a8f7bb4e1e839ca8047c5ad74e
