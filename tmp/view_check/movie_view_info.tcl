set __copilot_out_71490fde51214c398b1e3fe0a216041c [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/movie_view_info.txt" w]
set __copilot_remote_result_path "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/movie_view_info.txt.remote"
catch {file delete -force $__copilot_remote_result_path}
set rc [catch {
    package require dde
    dde execute TclEval IPG-MOVIE {
        set __copilot_remote_out [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/movie_view_info.txt.remote" w]
        set __copilot_remote_rc [catch {
            scan $View(ev.view) %d vno
            set wpath .view$vno
            set wi [$wpath.gl0 cget -width]
            set he [$wpath.gl0 cget -height]
            set vgeom [winfo geometry $wpath]
            set mgeom [wm geometry .]
            format "view_no=%d gl_size=%dx%d view_geom=%s movie_geom=%s" $vno $wi $he $vgeom $mgeom
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
    puts -nonewline $__copilot_out_71490fde51214c398b1e3fe0a216041c $__copilot_remote_payload
    catch {file delete -force $__copilot_remote_result_path}
} else {
    puts $__copilot_out_71490fde51214c398b1e3fe0a216041c "rc=$rc"
    puts $__copilot_out_71490fde51214c398b1e3fe0a216041c "msg_begin"
    puts $__copilot_out_71490fde51214c398b1e3fe0a216041c $msg
    puts $__copilot_out_71490fde51214c398b1e3fe0a216041c "msg_end"
}
close $__copilot_out_71490fde51214c398b1e3fe0a216041c
