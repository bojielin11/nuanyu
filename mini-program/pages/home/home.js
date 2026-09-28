const api = require('../../services/api')

Page({
  data: {
    state: null,
    loading: false,
    error: '',
    greeting: '正在连接小陪…',
    statusLines: [],
    lastUpdate: '',
    // 天气
    weather: null,
    weatherText: '',
    // 传感器
    sensors: null,
    sensorLines: [],
    sensorHealth: ''
  },

  _timer: null,

  onShow() {
    this.refresh()
    if (this._timer) clearInterval(this._timer)
    this._timer = setInterval(() => this.refresh(), 30000)
  },

  onHide() {
    if (this._timer) { clearInterval(this._timer); this._timer = null }
  },

  onUnload() {
    if (this._timer) { clearInterval(this._timer); this._timer = null }
  },

  async refresh() {
    this.setData({ loading: true, error: '' })
    try {
      const state = await api.status()
      let sensors = null
      let weather = null
      try { sensors = await api.sensors() } catch (e) {}
      try { weather = await api.weather() } catch (e) {}
      this._apply(state, sensors, weather)
    } catch (e) {
      let diag = ''
      try {
        if (api && typeof api.diagnose === 'function') {
          const d = await api.diagnose()
          const configured = (d.configured || []).map(x => (x.reachable ? '✓' : '✗') + (x.base || '?')).join(' ')
          diag = '配置探测: ' + configured +
            '\n缓存: ' + (d.cached || '无') +
            '\n自动发现: ' + (d.discovery || '未找到')
        } else {
          diag = 'api.diagnose 不存在（可能没重新编译）'
        }
      } catch (de) {
        diag = '诊断失败: ' + (de && de.message || de)
      }
      this.setData({
        error: (e && e.message) || '连接失败',
        state: null,
        greeting: '暂时无法连接小陪',
        statusLines: [
          { label: '错误', value: (e && e.message) || '未知', ok: false },
          { label: '诊断', value: diag, ok: false }
        ]
      })
    } finally {
      this.setData({ loading: false })
    }
  },

  go(e) {
    wx.switchTab({ url: e.currentTarget.dataset.url })
  },

  _apply(state, sensors, weather) {
    state = state || {}
    sensors = sensors || {}
    weather = weather || {}

    const now = new Date()
    const ts = now.getHours().toString().padStart(2, '0') + ':' +
               now.getMinutes().toString().padStart(2, '0') + ':' +
               now.getSeconds().toString().padStart(2, '0')

    const moodMap = { '开心': '😊', '一般': '😐', '疲惫': '😫', '焦虑': '😰', '崩溃': '💔' }
    const moodEmoji = moodMap[state.current_mood] || ''

    const ttsLoaded = !!(state.tts && state.tts.loaded) || !!(state.tts_engine && state.tts_engine.loaded)
    const ttsLabel = ttsLoaded
      ? ((state.tts && state.tts.speaker_label) || (state.tts_engine && state.tts_engine.speaker_label) || '正常')
      : '未加载'

    const statusLines = [
      { label: 'AI 服务',  value: state.ai_ok ? '正常' : '异常', ok: !!state.ai_ok },
      { label: '专注模式', value: state.study_running ? '运行中' : '未运行', ok: true },
      { label: '当前心情', value: (moodEmoji + ' ' + (state.current_mood || '—')).trim(), ok: true },
      { label: '小陪状态', value: state.assistant_sleeping ? '休息中' : '清醒', ok: !state.assistant_sleeping },
      { label: '用户在场', value: state.visual_state === 'PRESENT' ? '在场' : '离开中', ok: state.visual_state === 'PRESENT' },
      { label: '语音服务', value: ttsLabel, ok: ttsLoaded },
    ]

    const greeting = state.assistant_sleeping
      ? '小陪正在休息，说"你好小陪"叫醒我吧'
      : (state.visual_state === 'PRESENT' ? '我看到你了，今天一起加油～' : '你不在桌前，我在这里等你')

    // 天气（/api/weather）
    let weatherText = ''
    let weatherObj = null
    if (weather.ok && weather.temperature_c != null) {
      weatherObj = {
        city: weather.city || '上海',
        temp: Number(weather.temperature_c).toFixed(1),
        desc: weather.weather || '—'
      }
      weatherText = weatherObj.city + ' ' + weatherObj.temp + '°C · ' + weatherObj.desc
    }

    // 传感器（/api/sensors）
    let sensorLines = []
    let sensorHealth = ''
    const s = (sensors && sensors.sensors) || {}
    if (s && typeof s === 'object' && Object.keys(s).length) {
      sensorLines = [
        { label: '空间', value: s.presence ? '检测到人' : '当前无人' },
        { label: '温度', value: (Number(s.temperature_c) || 0).toFixed(1) + ' °C' },
        { label: '湿度', value: (Number(s.humidity_rh) || 0).toFixed(1) + ' %RH' },
        { label: '空气', value: Math.round(Number(s.air_ppb) || 0) + ' ppb' },
        { label: '光照', value: Math.round(Number(s.light) || 0) + ' lx' }
      ]
      sensorHealth = (sensors.health && sensors.health.connected) ? '环境感知在线' : '环境感知'
    }

    this.setData({
      state, greeting, statusLines, lastUpdate: ts, loading: false, error: '',
      weather: weatherObj, weatherText,
      sensors, sensorLines, sensorHealth
    })
  }
})
