set __copilot_out_08e72cb558d64e788dd2344ae9c96534 [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/search__movie.txt" w]
set __copilot_remote_result_path "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/search__movie.txt.remote"
catch {file delete -force $__copilot_remote_result_path}
set rc [catch {
    package require dde
    dde execute TclEval CarMaker {
        set __copilot_remote_out [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/search__movie.txt.remote" w]
        set __copilot_remote_rc [catch {
            catch {set w [winfo width .movie]} we
            catch {set h [winfo height .movie]} he
            catch {set ch2 [winfo children .movie]} ch2e
            format "pattern=.movie w=%s h=%s children=%s" $we $he $ch2e
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
    puts -nonewline $__copilot_out_08e72cb558d64e788dd2344ae9c96534 $__copilot_remote_payload
    catch {file delete -force $__copilot_remote_result_path}
} else {
    puts $__copilot_out_08e72cb558d64e788dd2344ae9c96534 "rc=$rc"
    puts $__copilot_out_08e72cb558d64e788dd2344ae9c96534 "msg_begin"
    puts $__copilot_out_08e72cb558d64e788dd2344ae9c96534 $msg
    puts $__copilot_out_08e72cb558d64e788dd2344ae9c96534 "msg_end"
}
close $__copilot_out_08e72cb558d64e788dd2344ae9c96534
