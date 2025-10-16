import logging
import math
import time

class ZOffsetCalculator:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()
        
        # 从配置文件读取参数
        self.pressure_switch_x = config.getfloat('pressure_switch_x', 30.0)
        self.pressure_switch_y = config.getfloat('pressure_switch_y', 200.0)
        self.compression_offset = config.getfloat('compression_offset', 0.2)
        self.approach_speed = config.getfloat('approach_speed', 5.0)
        self.retract_dist = config.getfloat('retract_dist', 5.0)
        self.dwell_time = config.getfloat('dwell_time', 2.0)  # 压力开关稳定时间
        self.max_probe_travel = config.getfloat('max_probe_travel', 20.0)  # 最大探测距离
        self.z_hop = config.getfloat("z_hop", 10.0)
        self.samples_tolerance = config.getfloat("samples_tolerance", 0.02)
        
        # 延迟初始化，确保所有对象都已加载
        self.printer.register_event_handler("klippy:connect", self._handle_connect)
        
        # 注册gcode命令
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('YUMI_CALCULATE_Z_OFFSET', self.cmd_CALCULATE_Z_OFFSET,
                              desc="Calculate probe z_offset using pressure switch")
    
    def _handle_connect(self):
        # 在Klipper连接完成后初始化所有依赖对象
        self.probe = self.printer.lookup_object('probe')
        
        # 获取探头偏移值 - 使用兼容API
        self.probe_x_offset = self.probe.get_offsets()[0]
        self.probe_y_offset = self.probe.get_offsets()[1]
               
        # 添加调试日志
        logging.info(f"[ZOffsetCalculator] Probe offsets: X={self.probe_x_offset}, Y={self.probe_y_offset}")

    
    
    def cmd_CALCULATE_Z_OFFSET(self, gcmd):
        logging.info("[ZOffsetCalculator] Starting z_offset calculation")
        toolhead = self.printer.lookup_object('toolhead')
        
        gcode = self.printer.lookup_object('gcode')
        gcode.run_script_from_command("G28")
        
        # 用接近开关执行探测
        gcmd.respond_info("Probing with proximity sensor...")
        proximity_z = self.probe_with_proximity_sensor()
        
        # 移动到压力开关位置
        gcmd.respond_info("Moving to pressure switch position...")
        self.move_to_pressure_switch()
        gcode.run_script_from_command("M400")
        gcode.run_script_from_command("G4 P1000")
        
        # 用压力开关执行探测
        gcmd.respond_info("Probing with pressure switch...")
        pressure_z = self.probe_with_pressure_switch(gcmd)
        
        # 计算z_offset
        z_offset = proximity_z - pressure_z + self.compression_offset
        gcmd.respond_info(f"Calculated z_offset: {z_offset:.4f}")
        
        # 移动到压力开关位置
        gcmd.respond_info("Moving to pressure switch position...")
        self.move_to_pressure_switch()
        
        # 保存z_offset到配置文件
        self.save_z_offset(z_offset)
        
        gcmd.respond_info("Z offset calculation complete!")
        logging.info(f"[ZOffsetCalculator] Completed z_offset calculation: {z_offset:.4f}")
    
    def move_to_pressure_switch(self):
        # 移动到压力开关上方安全高度
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.manual_move([self.pressure_switch_x, self.pressure_switch_y, 10.0], 50.0)
        logging.info(f"[ZOffsetCalculator] Moved to pressure switch position: X={self.pressure_switch_x}, Y={self.pressure_switch_y}")
    
    def probe_with_proximity_sensor(self):
        # 使用接近开关进行探测
        toolhead = self.printer.lookup_object('toolhead')
        gcode = self.printer.lookup_object('gcode')
        
        # 应用探头偏移的反向移动
        actual_x = self.pressure_switch_x - self.probe_x_offset
        actual_y = self.pressure_switch_y - self.probe_y_offset
        
        # 移动到修正后的位置
        toolhead.manual_move([actual_x, actual_y, 10.0], 50.0)
        
        # 执行探测 - 使用PROBE命令
        gcode.run_script_from_command("PROBE")
        
        # 获取当前Z位置
        pos = toolhead.get_position()
        logging.info(f"[ZOffsetCalculator] Proximity probe result: Z={pos[2]}")
        
        return pos[2]
    
    def probe_with_pressure_switch(self, gcmd=None):
        """使用压力开关探测Z高度"""
        toolhead = self.printer.lookup_object('toolhead')
        zendstop_p = self.printer.lookup_object('probe_pressure').run_probe(gcmd)
        
        reprobe_cnt = 1
        while True:
            if(reprobe_cnt >= 6):
                gcmd.respond_info("ZoffsetCalibration: Pressure probe more than five times.")
                raise gcmd.error('ZoffsetCalibration: Pressure probe more than five times.')
            # Perform Z Hop
            if self.z_hop:
                pos = toolhead.get_position()
                pos[2] += self.z_hop
                toolhead.manual_move([None, None, pos[2]], 5)
            gcmd.respond_info("ZoffsetCalibration: Pressure verifying the difference between before and after %d/5." % (reprobe_cnt))
            zendstop_p1 = self.printer.lookup_object('probe_pressure').run_probe(gcmd)
            diff_z = abs(zendstop_p1[2] - zendstop_p[2])
            zendstop_p = zendstop_p1
            if diff_z <= self.samples_tolerance:
                gcmd.respond_info("ZoffsetCalibration: Pressure check success.")
                break
            reprobe_cnt += 1
            
        logging.info(f"[ZOffsetCalculator] Pressure switch triggered at Z={zendstop_p[2]}")
        return zendstop_p[2]
    
    def save_z_offset(self, z_offset):
        # 保存z_offset到配置文件
        configfile = self.printer.lookup_object('configfile')
        configfile.set('probe', 'z_offset', f"{z_offset:.4f}")
        
        # 通知用户
        gcode = self.printer.lookup_object('gcode')
        gcode.respond_info(f"Z offset saved to config file: {z_offset:.4f}")
        logging.info(f"[ZOffsetCalculator] Z offset saved: {z_offset:.4f}")
        gcode.run_script_from_command("SAVE_CONFIG")

def load_config(config):
    return ZOffsetCalculator(config)