const api = require('../../services/api')
Page({
  data: { goal: '', running: false, loading: false, suggestion: '给今天一个清晰的小目标吧。' },

  onShow() {
    api.status().then(state => {
      this.setData({
        running: !!state.study_running,
        goal: state.study_goal && state.study_goal !== '待填写' ? state.study_goal : this.data.goal || ''
      })
    }).catch(() => {})
  },

  input(e) { this.setData({ goal: e.detail.value }) },

  async start() {
    const goal = this.data.goal.trim()
    if (!goal) return wx.showToast({ title: '先写下专注目标', icon: 'none' })
    this.setData({ loading: true })
    try {
      const r = await api.command('study')
      this.setData({ running: true, suggestion: r.reply || '专注已开始，小陪会陪着你。' })
    } catch (e) {
      wx.showToast({ title: e.message, icon: 'none' })
    } finally { this.setData({ loading: false }) }
  },

  async stop() {
    this.setData({ loading: true })
    try {
      const r = await api.command('stop')
      this.setData({ running: false, suggestion: r.reply || '这一段专注辛苦了。' })
    } catch (e) {
      wx.showToast({ title: e.message, icon: 'none' })
    } finally { this.setData({ loading: false }) }
  }
})
