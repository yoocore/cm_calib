set __copilot_command_script "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/script_control_apply.tcl"
set __copilot_result_path "C:/CM_Projects/CMO141_Calibration/SimOutput/script_control_camera_apply_result.txt"
set __copilot_rc [catch {uplevel #0 [list RunScript $__copilot_command_script]} __copilot_msg]
if {$__copilot_rc != 0} {
    set __copilot_runtime_out [open $__copilot_result_path w]
    puts $__copilot_runtime_out "rc=$__copilot_rc"
    puts $__copilot_runtime_out "msg_begin"
    puts $__copilot_runtime_out $__copilot_msg
    puts $__copilot_runtime_out "msg_end"
    close $__copilot_runtime_out
}
