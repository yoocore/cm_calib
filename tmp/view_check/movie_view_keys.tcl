set __copilot_out_e758425b18824f1a9ecc9617dbfcb936 [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/movie_view_keys.txt" w]
set __copilot_remote_result_path "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/movie_view_keys.txt.remote"
catch {file delete -force $__copilot_remote_result_path}
set rc [catch {
    package require dde
    dde execute TclEval IPG-MOVIE {
        set __copilot_remote_out [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/movie_view_keys.txt.remote" w]
        set __copilot_remote_rc [catch {
            set keys [array names View]
            set first20 [lrange $keys 0 19]
            format "count=%d first20=%s" [llength $keys] $first20
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
    puts -nonewline $__copilot_out_e758425b18824f1a9ecc9617dbfcb936 $__copilot_remote_payload
    catch {file delete -force $__copilot_remote_result_path}
} else {
    puts $__copilot_out_e758425b18824f1a9ecc9617dbfcb936 "rc=$rc"
    puts $__copilot_out_e758425b18824f1a9ecc9617dbfcb936 "msg_begin"
    puts $__copilot_out_e758425b18824f1a9ecc9617dbfcb936 $msg
    puts $__copilot_out_e758425b18824f1a9ecc9617dbfcb936 "msg_end"
}
close $__copilot_out_e758425b18824f1a9ecc9617dbfcb936
