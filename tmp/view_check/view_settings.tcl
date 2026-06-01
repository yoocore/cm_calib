set __copilot_out_192785afa53f44eab7b22c9e86486740 [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/view_settings.txt" w]
set __copilot_remote_result_path "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/view_settings.txt.remote"
catch {file delete -force $__copilot_remote_result_path}
set rc [catch {
    package require dde
    dde execute TclEval CarMaker {
        set __copilot_remote_out [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/view_settings.txt.remote" w]
        set __copilot_remote_rc [catch {
            scan $View(ev.view) %d vno
            set wpath .view$vno
            set wi [$wpath.gl0 cget -width]
            set he [$wpath.gl0 cget -height]
            format "gl_size=%dx%d view_geom=%s movie_geom=%s view_w=%d view_h=%d" $wi $he [winfo geometry $wpath] [wm geometry .] [dict get $View($vno) Width] [dict get $View($vno) Height]
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
    puts -nonewline $__copilot_out_192785afa53f44eab7b22c9e86486740 $__copilot_remote_payload
    catch {file delete -force $__copilot_remote_result_path}
} else {
    puts $__copilot_out_192785afa53f44eab7b22c9e86486740 "rc=$rc"
    puts $__copilot_out_192785afa53f44eab7b22c9e86486740 "msg_begin"
    puts $__copilot_out_192785afa53f44eab7b22c9e86486740 $msg
    puts $__copilot_out_192785afa53f44eab7b22c9e86486740 "msg_end"
}
close $__copilot_out_192785afa53f44eab7b22c9e86486740
