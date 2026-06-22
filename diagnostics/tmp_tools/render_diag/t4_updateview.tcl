set __copilot_out_5986e462f4a645cd83fbc57cb69f0cd3 [open "tmp/render_diag/t4_updateview.txt" w]
set __copilot_remote_result_path "tmp/render_diag/t4_updateview.txt.remote"
catch {file delete -force $__copilot_remote_result_path}
set rc [catch {
    package require dde
    dde execute TclEval IPG-MOVIE {
        set __copilot_remote_out [open "tmp/render_diag/t4_updateview.txt.remote" w]
        set __copilot_remote_rc [catch {
            scan $View(ev.view) %d vno
            set rc [catch {UpdateView $vno} msg]
            after 100
            set uc $::View(UpdateCounter)
            list RC $rc MSG $msg UC $uc
        } __copilot_remote_msg]
        puts $__copilot_remote_out "rc=$__copilot_remote_rc"
        puts $__copilot_remote_out "msg_begin"
        puts $__copilot_remote_out $__copilot_remote_msg
        puts $__copilot_remote_out "msg_end"
        close $__copilot_remote_out
        if {$__copilot_remote_rc != 0} {error $__copilot_remote_msg}
    }
} msg]
set __copilot_remote_wait_deadline [expr {[clock milliseconds] + 1000}]
while {![file exists $__copilot_remote_result_path] && [clock milliseconds] < $__copilot_remote_wait_deadline} {
    after 25
}
if {[file exists $__copilot_remote_result_path]} {
    set __copilot_remote_in [open $__copilot_remote_result_path r]
    set __copilot_remote_payload [read $__copilot_remote_in]
    close $__copilot_remote_in
    puts -nonewline $__copilot_out_5986e462f4a645cd83fbc57cb69f0cd3 $__copilot_remote_payload
    catch {file delete -force $__copilot_remote_result_path}
} else {
    puts $__copilot_out_5986e462f4a645cd83fbc57cb69f0cd3 "rc=$rc"
    puts $__copilot_out_5986e462f4a645cd83fbc57cb69f0cd3 "msg_begin"
    puts $__copilot_out_5986e462f4a645cd83fbc57cb69f0cd3 $msg
    puts $__copilot_out_5986e462f4a645cd83fbc57cb69f0cd3 "msg_end"
}
close $__copilot_out_5986e462f4a645cd83fbc57cb69f0cd3
