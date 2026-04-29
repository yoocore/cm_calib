set __copilot_command_script "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/script_control_apply.tcl"
set __copilot_result_path "C:/CM_Projects/CMO141_Calibration/SimOutput/script_control_camera_apply_result.txt"
set __copilot_rc [catch {uplevel #0 [list RunScript $__copilot_command_script]} __copilot_msg]
if {$__copilot_rc != 0} {
    set out [open $__copilot_result_path w]
    puts $out "rc=$__copilot_rc"
    puts $out "msg_begin"
    puts $out $__copilot_msg
    puts $out "msg_end"
    close $out
}
