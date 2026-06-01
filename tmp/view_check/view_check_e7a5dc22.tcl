set __co_d3fe4139 [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/view_settings.txt" w]
set __remote "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/view_settings.remote"
catch {file delete -force $__remote}
set rc [catch {
    package require dde
    dde execute TclEval IPG-MOVIE {
        set __rout [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/view_settings.remote" w]
        set __rrc [catch {
            scan $View(ev.view) %d vno
            set wpath .view$vno
            set wi [$wpath.gl0 cget -width]
            set he [$wpath.gl0 cget -height]
            set vgeom [winfo geometry $wpath]
            set wgeom [winfo geometry .]
            set mgeom [wm geometry .]
            set vinfo [info exists View($vno)]
            format "view_no=$vno;gl_width=$wi;gl_height=$he;view_geom=$vgeom;win_geom=$wgeom;movie_geom=$mgeom;view_exists=$vinfo"
        } __rmsg]
        puts $__rout "rc=$__rrc"
        puts $__rout "msg_begin"
        puts $__rout $__rmsg
        puts $__rout "msg_end"
        close $__rout
        if {$__rrc != 0} {error $__rmsg}
    }
} msg]
if {[file exists $__remote]} {
    set __rin [open $__remote r]
    set __rp [read $__rin]
    close $__rin
    puts -nonewline $__co_d3fe4139 $__rp
    catch {file delete -force $__remote}
} else {
    puts $__co_d3fe4139 "rc=$rc"
    puts $__co_d3fe4139 "msg_begin"
    puts $__co_d3fe4139 $msg
    puts $__co_d3fe4139 "msg_end"
}
close $__co_d3fe4139