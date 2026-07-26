/* richmor_codec_native.js — browser mirror of the native MDVR LAN config sections.
 * Rule 0: section/field metadata lives here (not in the HTML). Field lists are transcribed from the
 * iVehicle APK decoders; LABELS are the APK's own English strings (res/values-en/strings.xml),
 * verbatim — not invented. Grouped into the same categories the OEM Settings menu uses.
 *
 * field: {k:key, n:label, t:'num'|'text'|'sel'|'enum', u:unit, d:desc, opts?}
 * section: {param, group, title, icon, fields:[...], slots?:{count,label,fields:[...]}, note?, noquery?}
 */
window.RNAT = (function () {
  const f = (k, n, t, u, d) => ({ k, n, t: t || 'num', u: u || '', d: d || k });
  const on = 'sel';   // 0/1 boolean -> toggle
  const PROTO = [[1,'JT808-1'],[2,'JT808-2'],[4,'JT808-SB'],[5,'TEX'],[11,'RCMMT'],[12,'JT808-SB2'],
    [13,'JT808-SB3'],[15,'HUOYUN'],[17,'GZZT'],[20,'JT808-YB'],[21,'JT808-YB2'],[30,'BB-YW']];
  const en = (k, n, d) => ({ k, n, t: 'enum', u: '', d: d || k, opts: PROTO });
  const fe = (k, n, opts, u, d) => ({ k, n, t: 'enum', u: u || '', d: d || k, opts });   // enum with explicit options
  // enum option lists — value(index) -> label, transcribed VERBATIM from the APK select dialogs
  const E = {
    // Recording / encoding
    channel_type:    [[0,'None'],[1,'Simulation'],[2,'IPC']],
    encoding_format: [[0,'H264'],[1,'H265']],
    resolution:      [[0,'CIF'],[1,'HD1'],[2,'D1'],[3,'720P'],[4,'1080P']],
    stream_encoding: [[0,'CBR'],[1,'VBR']],
    audio_format:    [[0,'G711A'],[1,'G726']],
    video_format:    [[0,'PAL'],[1,'NTSC']],
    video_model:     [[0,'Boot video'],[1,'Timed recording'],[2,'Alarm recording'],[3,'3 days video']],
    disp_res:        [[0,'720×576'],[1,'1024×768'],[2,'1280×720'],[3,'1920×1080']],
    video_use:       [[0,'No'],[1,'Main stream video'],[2,'Sub stream video'],[3,'Alarm video']],
    disk_priority:   [[0,'High'],[1,'Low']],
    // System
    date_format:     [[0,'Y-M-D'],[1,'D-M-Y'],[2,'M-D-Y']],
    time_sync:       [[0,'Off'],[1,'GPS'],[2,'NTP']],
    timeout:         [[0,'1 Minute'],[1,'2 Minutes'],[2,'5 Minutes'],[3,'10 Minutes']],
    language:        [[0,'Chinese'],[1,'English'],[2,'Traditional Chinese'],[3,'Russian'],[4,'Korean'],[5,'Portuguese'],[6,'Spanish'],[7,'French']],
    power_model:     [[0,'Timing mode'],[1,'Standby mode'],[2,'Sleep mode'],[3,'Deep sleep']],
    multi_screen:    [[0,'Single screen'],[1,'Two screen'],[2,'Three screen'],[3,'Four screen'],[4,'Five screen'],[5,'Six screen'],[6,'Eight screen'],[7,'Nine screen']],
    // Network
    net_type:        [[0,'WCDMA'],[1,'EVDO'],[2,'TD-SCDMA'],[3,'TDDLTE'],[4,'FDDLTE-W'],[5,'FDDLTE-C'],[6,'EVDO-T']],
    auth_mode:       [[0,'CHAP'],[1,'PAP']],
    search_mode:     [[0,'Automatic'],[1,'4G']],
    wifi_auth:       [[0,'Open'],[1,'Share'],[2,'WPA'],[3,'WPA-PSK']],
    wifi_enc:        [[0,'None'],[1,'WEP'],[2,'TKIP'],[3,'AES']],
    wifi_type:       [[0,'Station'],[1,'AP']],
    connect_type:    [[0,'Local network'],[1,'WIFI'],[2,'Peripheral']],
    // Alarm
    speed_source:    [[0,'GPS speed'],[1,'Pulse speed'],[2,'Mixing speed']],
    speed_unit:      [[0,'km/h'],[1,'MPH']],
    limit_type:      [[0,'Entire journey'],[1,'Segmentation']],
    trigger_level:   [[0,'Low level'],[1,'High level']],
    temp_unit:       [[0,'Celsius'],[1,'Fahrenheit']],
    linkage:         [[0,'OFF'],[1,'Level output 1'],[2,'Level output 2'],[3,'Level output 3'],[4,'Level output 4']],   // IO alarm linkage: off + 4 outputs
    linkage2:        [[0,'OFF'],[1,'Level output 1'],[2,'Level output 2']],   // Speed/Temp/Accel/Voltage alarm linkage: off + 2 outputs
    // Peripheral / AI
    check_digit:     [[0,'None'],[1,'Odd'],[2,'Even']],
    blind_area:      [[0,'Rear'],[1,'Left rear'],[2,'Right rear'],[3,'Ahead']],
    sound_channel:   [[0,'Left channel'],[1,'Right channel']],   // speaker: audio output channel
    speaker_out:     [[0,'Outside speakers'],[1,'Inner horn']],   // speaker: which physical speaker
    // serial port — coded values (from AppearanceSerialPortSetting): stop bit = stored*0.5+0.5, baud = index
    data_bit:        [[7,'7'],[8,'8'],[9,'9']],
    stop_bit:        [[0,'0.5'],[1,'1'],[2,'1.5'],[3,'2']],
    baud_rate:       [[0,'2400'],[1,'4800'],[2,'9600'],[3,'19200'],[4,'38400'],[5,'57600'],[6,'115200']],
    // IO alarm sensor type (IOTypeUtils str_alarm_1..31; 0 = none) — APK verbatim
    io_type:         [[0,'(None)'],[1,'Emergency alarm'],[2,'Front door'],[3,'Middle gate'],[4,'Back door'],
      [5,'Driving door'],[6,'Other doors'],[7,'Low beam'],[8,'High beam'],[9,'Right turn'],[10,'Left turn'],
      [11,'Brake'],[12,'Reverse'],[13,'Fog light'],[14,'Marker light'],[15,'Horn'],[16,'Air conditioning'],
      [17,'Neutral gear'],[18,'Retarder'],[19,'ABS'],[20,'Heater'],[21,'Clutch'],[22,'Door sensor'],[23,'Smoke'],
      [24,'Custom 1'],[25,'Custom 2'],[26,'Custom 3'],[27,'Custom 4'],[28,'Custom 5'],[29,'Custom 6'],[30,'Custom 7'],[31,'Custom 8']],
  };
  // shared alarm-level fields (Speed/Temperature/Acceleration/Voltage)
  // Speed/Temperature/Acceleration/Voltage share this sub-block (APK SpeedSubResMessage). Per the OEM
  // activities: Enable + Video are booleans (0=OFF/1=ON), Alarm linkage is off + 2 level outputs.
  const LVL = (thN) => [ f('switch','Enable',on), f('threshold', thN||'Threshold'),
    f('duration','Duration','','s'), f('video_linkage','Video',on), fe('alarm_linkage','Alarm linkage',E.linkage2) ];

  const SECTIONS = [
    // ─────────── SYSTEM ───────────
    { param: 0xF003, group: 'System', title: 'Terminal information', icon: 'badge', fields: [
      f('device_no','Device number','text'), f('phone','Phone number','text'), f('plate','License plate','text'),
      f('plate_color','Plate color'), f('device_type','Vehicle type','text'), f('province_id','Province ID','text'),
      f('city_id','City ID','text'), f('frame_no','Device frame number','text'), f('date','Date','text'),
      f('company','Company name','text'), f('service_line','Service line','text'), f('terminal_model','Terminal model','text'),
      f('vendor_id','Manufacturer ID','text'), f('terminal_id','Terminal ID','text'),
      f('cccid','CCCID','text'), f('locomotive_no','Locomotive number','text') ] },
    { param: 0xF001, group: 'System', title: 'Date time', icon: 'schedule', fields: [
      fe('time_format','Time format', E.date_format), fe('time_sync','Time sync', E.time_sync), fe('timeout','Timeout', E.timeout),
      fe('language','Language', E.language), f('time_zone','Time zone'), f('date','Date','date'), f('time','Time','time') ] },
    { param: 0xF000, group: 'System', title: 'Power management', icon: 'power_settings_new', fields: [
      fe('power_model','Switching mode', E.power_model), fe('multi_screen','Multi-screen', E.multi_screen),
      f('screensaver_delay','Delayed screensaver','','s'), f('shutdown_delay','Delay shutdown','','s'),
      f('boot_time','Boot time','time'), f('shutdown_time','Shutdown time','time'), f('channel','Channel') ] },
    { param: 0xF004, group: 'System', title: 'Function switch', icon: 'toggle_on', fields: [
      f('security_voice','Security voice reminder',on), f('start_snapshot','Start driving snapshot',on),
      f('standard_switch','Standard switch',on), f('positioning_assist','Positioning assistance',on),
      f('startup_tone','Startup tone',on), f('location_mode','Location mode'),
      f('su_standard_upload','SU standard upload',on), f('lcd_backlight','LCD backlight'),
      f('ntrip_port','NTRIP port'), f('ntrip_ip','NTRIP IP','text'), f('ntrip_user','NTRIP username','text'),
      f('ntrip_password','NTRIP password','text'), f('ntrip_mount_point','NTRIP mount point','text') ] },
    { param: 0xF002, group: 'System', title: 'User Management', icon: 'manage_accounts', fields: [
      f('password_enabled','Password enable',on), f('admin_password','Admin password','text'),
      f('user_password','User password','text') ] },

    // ─────────── RECORDING ───────────
    { param: 0xF023, group: 'Recording', title: 'Basic settings', icon: 'settings_video_camera', fields: [
      fe('video_format','Video format', E.video_format), fe('video_model','Video mode', E.video_model), f('audio_gain','Audio gain'),
      f('alarm_pre_record','Alarm pre-recording','','s'), f('alarm_delay','Alarm delay','','s'),
      f('alarm_file_protect','Alarm file protection'), fe('display_resolution','Display resolution', E.disp_res),
      f('osd_overlay','OSD overlay enable selection') ] },
    { param: 0xF024, group: 'Recording', title: 'Encoding settings', icon: 'hd',
      fields: [ f('max_channel','Max channel'), fe('audio_format','Audio encoding format', E.audio_format) ],
      slots: { count: 16, label: 'Channel', fields: [
        fe('channel_type','Channel type', E.channel_type), fe('encoding_format','Encoding format', E.encoding_format),
        fe('main_resolution','Main stream resolution', E.resolution), f('main_frame_rate','Main stream frame rate','','fps'),
        f('main_quality','Main stream quality'), f('main_record','Main stream recording',on),
        fe('main_bitrate_type','Main stream encoding', E.stream_encoding), fe('sub_resolution','Sub stream resolution', E.resolution),
        f('sub_frame_rate','Sub stream frame rate','','fps'), f('sub_quality','Sub stream quality'),
        f('sub_record','Sub stream recording',on), fe('sub_bitrate_type','Sub stream encoding', E.stream_encoding),
        f('port','Port'), f('ip','IP','text'), f('user','Username','text'), f('password','Password','text') ] } },
    { param: 0xF026, group: 'Recording', title: 'Disk management', icon: 'storage', slots: { count: 4, label: 'Disk', fields: [
      fe('video_use','Video use', E.video_use), fe('priority','Priority', E.disk_priority) ] } },
    { param: 0xF025, group: 'Recording', title: 'Schedule video', icon: 'timer', slots: { count: 8, label: 'Slot', fields: [
      f('start_time1','Start time 1','time'), f('end_time1','End time 1','time'),
      f('start_time2','Start time 2','time'), f('end_time2','End time 2','time') ] } },

    // ─────────── NETWORK ───────────
    { param: 0xF005, group: 'Network', title: 'Center settings', icon: 'dns', fields: [],
      slots: { count: 8, label: 'Platform', fields: [
        f('enabled','Enable',on), en('server_type','Server type'), f('main_ip','Main domain / IP','text'),
        f('main_port','Main port'), f('sub_ip','Sub domain / IP','text'), f('sub_port','Sub port') ] } },
    { param: 0xF006, group: 'Network', title: 'Local settings', icon: 'lan', fields: [
      fe('connect_type','Connect type', E.connect_type), f('ip','IP','text'), f('mask','Mask','text'), f('gateway','Gateway','text'),
      f('dns1','DNS1','text'), f('dns2','DNS2','text'), f('mac','MAC','text') ] },
    { param: 0xF007, group: 'Network', title: '3G/4G settings', icon: 'signal_cellular_alt', fields: [
      f('enable','Enable',on), fe('type','Type', E.net_type), fe('auth_mode','Authentication mode', E.auth_mode), f('private_dialing','Private network dialing',on),
      f('apn','APN','text'), f('center_number','Center number','text'), f('sms_center','SMS center','text'),
      f('username','Username','text'), f('password','Password','text'), fe('search_mode','Search mode', E.search_mode) ] },
    { param: 0xF008, group: 'Network', title: 'WiFi settings', icon: 'wifi', fields: [
      f('encryption_switch','Encryption switch',on), fe('auth_mode','Authentication mode', E.wifi_auth), fe('encryption_type','Encryption type', E.wifi_enc),
      f('dhcp','Automatic acquisition',on), f('ap_ssid','AP SSID','text'), f('ap_password','AP password','text'),
      f('ap_ip','AP IP','text'), f('ap_gateway','AP gateway','text'), f('ap_mask','AP mask','text'),
      f('station_ssid','Station SSID','text'), f('station_password','Station password','text'),
      f('station_ip','Station IP','text'), f('station_gateway','Station gateway','text'), f('station_mask','Station mask','text') ] },
    { param: 0xF00A, group: 'Network', title: 'FTP settings', icon: 'cloud_upload', fields: [
      f('ftp_port','FTP port'), f('ftp_ip','FTP IP','text'), f('ftp_user','FTP username','text'), f('ftp_password','FTP password','text') ] },

    // ─────────── ALARM ───────────
    { param: 0xF00B, group: 'Alarm', title: 'IO alarm', icon: 'input', slots: { count: 16, label: 'IO', fields: [
      fe('io_type','Type', E.io_type), fe('trigger_level','Trigger level', E.trigger_level), f('delay','Delay','','s'),
      f('video_linkage','Video', on), fe('alarm_linkage','Alarm linkage', E.linkage),
      f('preview_linkage','Preview linkage'), f('holding_time','Maint Time','','s'),
      // present only on firmware that sends 12-byte IO rows (opt:1 -> shown only if the device returned them)
      Object.assign(f('evidence_channel','Evidence Ch'), {opt:1}),
      Object.assign(f('forensic_capture','Forensic capture', on), {opt:1}),
      Object.assign(f('forensic_video','Forensic video', on), {opt:1}) ] } },
    { param: 0xF00C, group: 'Alarm', title: 'Speed alarm', icon: 'speed', fields: [
      fe('source','Speed source', E.speed_source), fe('unit','Speed unit', E.speed_unit), fe('limit_type','Limit type', E.limit_type), f('night_limit','Night enable', on),
      f('pulse_factor','Pulse factor'), f('driven_distance','Driving distance'),
      f('limit_value','Speed limit value','','km/h'), f('start_time','Start time','time'), f('end_time','End time','time') ],
      // the 5 rows are fixed alarm types (not "levels"), named exactly as the OEM app (AlarmSpeedSetting.types)
      slots: { count: 5, label: 'Type', types: ['Overtime parking','Low speed alarm','Low speed warning','High speed warning','High speed alarm'], fields: LVL() } },
    { param: 0xF00D, group: 'Alarm', title: 'Temperature alarm', icon: 'device_thermostat', fields: [ fe('unit','Temperature unit', E.temp_unit) ],
      slots: { count: 2, label: 'Type', types: ['Low temperature','High temperature'], fields: LVL() } },
    { param: 0xF00E, group: 'Alarm', title: 'Acceleration', icon: 'vibration', fields: [ f('calibrated','Whether to calibrate',on) ],
      slots: { count: 5, label: 'Type', types: ['X axis','Y axis','Z axis','Collision settings','Rollover settings'], fields: LVL() } },
    { param: 0xF00F, group: 'Alarm', title: 'Voltage setting', icon: 'bolt', fields: [ f('delay_shutdown','Delay shutdown','','s') ],
      slots: { count: 2, label: 'Type', types: ['High voltage alarm','Low pressure alarm'], fields: LVL('Threshold') } },
    { param: 0xF010, group: 'Alarm', title: 'Motion detection', icon: 'motion_photos_on', fields: [
      f('alarm_interval','Alarm interval','','s'), f('snap_switch','Snapshot',on), f('snapshots','Snapshots'),
      f('capture_interval','Capture interval','','ms'), f('channel_num','Channel count') ] },

    // ─────────── PERIPHERAL ───────────
    { param: 0xF015, group: 'Peripheral', title: 'Fuel Gauge Settings', icon: 'local_gas_station', fields: [
      f('tank_capacity','Fuel tank capacity','','L') ] },
    // the 7 rows are fixed "business functions" (AppearanceSpeakerSetting.strings); channel/speaker are enums
    { param: 0xF016, group: 'Peripheral', title: 'Speaker settings', icon: 'volume_up', slots: { count: 7, label: 'Function',
      types: ['TTS broadcast','IP intercom','Voice call','Shouting in the car','Video playback','Native preview','Voice station'],
      fields: [ f('priority','Priority'), fe('channel','Sound channel', E.sound_channel), fe('speaker','Speaker', E.speaker_out) ] } },
    { param: 0xF018, group: 'Peripheral', title: 'Serial port settings', icon: 'cable', countKey: 'serial_port_num',
      fields: [ f('serial_port_num','Serial port count') ],
      slots: { count: 4, label: 'Port', fields: [
        // "enable" is really the connected Peripheral type id (0 = none); its names live in F017, which
        // we don't read on this device. data bit / stop bit / baud rate are coded pickers, not free numbers.
        f('enable','Peripheral'), fe('data_bit','Data bit', E.data_bit), fe('stop_bit','Stop bit', E.stop_bit),
        fe('check_digit','Check digit', E.check_digit), fe('baud_rate','Baud rate', E.baud_rate) ] } },

    // ─────────── AI ───────────
    { param: 0xF100, group: 'AI', title: 'DSM settings', icon: 'face', fields: [
      f('enable','Enable',on), f('alarm_video','Alarm recording',on), f('debug_mode','Debug mode',on), f('snap_enable','Alarm snapshot',on),
      f('channel','Associated channel'), f('delay','Delay time','','ms'), f('duration','Duration','','ms'),
      f('l1_speed','Level 1 alarm speed','','km/h'), f('l2_speed','Level 2 alarm speed','','km/h'), f('fatigue','Fatigue driving',on),
      f('fatigue_threshold','Fatigue alarm opening threshold'), f('fatigue_interval','Physiological fatigue alarm interval','','s'),
      f('yawn','Yawn',on), f('eyes_closed','Close your eyes',on), f('smoke','Smoke',on),
      f('smoke_threshold','Smoke alarm opening threshold'), f('smoke_interval','Smoking alarm interval','','s'),
      f('phone','Phone',on), f('call_threshold','Call alarm on threshold'), f('call_interval','Call alarm interval','','s'),
      f('distraction','Distraction alarm',on), f('distraction_threshold','Distraction alarm on threshold'),
      f('distraction_interval','Distraction alarm trigger interval','','s'), f('look_left','Look left',on), f('look_right','Look right',on),
      f('head_up','Raise your head',on), f('head_down','Bow your head',on), f('driver_abnormal','Driver abnormal',on),
      f('abnormal_threshold','Driver abnormal alarm opening threshold'), f('abnormal_interval','Driver abnormal alarm interval','','s'),
      f('no_face','Driver alarm face not detected',on), f('off_seat','Misalignment off seat',on), f('sunglasses','Blocking sunglasses',on),
      f('mouth_occlusion','Mouth occlusion',on), f('shield_interval','Shielding failure alarm interval','','s') ] },
    { param: 0xF101, group: 'AI', title: 'ADAS settings', icon: 'directions_car', fields: [
      f('enable','Enable',on), f('alarm_video','Alarm recording',on), f('channel','Associated channel'), f('snap_enable','Alarm snapshot',on),
      f('delay','Delay time','','ms'), f('duration','Duration','','ms'), f('report_interval','Alarm reporting interval','','s'),
      f('l1_speed','Level 1 alarm speed','','km/h'), f('l2_speed','Level 2 alarm speed','','km/h'),
      f('left_ldw','Left lane departure',on), f('right_ldw','Right lane departure',on),
      f('ldw_threshold','Left and right lane line distance alarm threshold'), f('fcw','Front vehicle collision',on),
      f('fcw_threshold','FCW alarm on threshold'), f('pcw','Pedestrian collision',on), f('pcw_threshold','PCW on threshold'),
      f('hmw','Distance detection',on), f('hmw_threshold','HMW on threshold') ] },
    { param: 0xF102, group: 'AI', title: 'BSD settings', icon: 'sensors', fields: [
      f('enable','Enable',on), f('alarm_video','Alarm recording',on), f('curb_detection','Curb detection',on),
      f('channel','Associated channel'), f('snap_enable','Alarm snapshot',on), f('delay','Delay time','','ms'), f('duration','Duration','','ms'),
      f('preview_switch','Preview switch',on), fe('blind_area_attr','Blind area attribute', E.blind_area) ] },
    { param: 0xF103, group: 'AI', title: 'BSD2 settings', icon: 'sensors', fields: [
      f('enable','Enable',on), f('alarm_video','Alarm recording',on), f('curb_detection','Curb detection',on),
      f('channel','Associated channel'), f('snap_enable','Alarm snapshot',on), f('delay','Delay time','','ms'), f('duration','Duration','','ms'),
      f('preview_switch','Preview switch',on), fe('blind_area_attr','Blind area attribute', E.blind_area) ] },
    { param: 0xF105, group: 'AI', title: 'COV setting', icon: 'photo_camera', fields: [
      f('enable','Enable',on), f('channel','Associated channel'), f('accuracy','Accuracy'),
      f('inhibition_time','Inhibition time','','s'), f('cycle','Cycle','','s') ] },
    { param: 0xF104, group: 'AI', title: 'Top DSM', icon: 'airline_seat_recline_normal', fields: [
      f('enable','Enable',on), f('alarm_video','Alarm recording',on), f('channel','Associated channel'), f('snap_enable','Alarm snapshot',on),
      f('delay','Delay time','','ms'), f('duration','Duration','','ms'), f('alarm_speed','Alarm opening speed','','km/h'),
      f('seatbelt','Seat belt not fastened',on), f('phone_play','Play with the mobile phone',on), f('wheel_off','Disengage the steering wheel',on),
      f('unbelt_threshold','Unbelted opening threshold'), f('phone_threshold','Opening threshold of playing mobile phone'),
      f('wheel_off_threshold','Off steering wheel opening threshold'), f('unbelt_interval','Unbelted alarm interval','','s'),
      f('phone_interval','Play phone alarm interval','','s'), f('wheel_off_interval','Off steering wheel alarm interval','','s') ] },
  ];

  return { GROUPS: ['System', 'Recording', 'Network', 'Alarm', 'Peripheral', 'AI'], SECTIONS };
})();
