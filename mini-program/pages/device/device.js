const api = require('../../services/api')

Page({
  data: { state: null, loading: false, sleepToggling: false },

  onShow() { this.refresh() },

  async refresh() {
    this.setData({ loading: true })
    try {
      this.setData({ state: await api.status() })
    } catch (e) {
      wx.showToast({ title: e.message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  async command(e) {
    try {
      const r = await api.command(e.currentTarget.dataset.command)
      wx.showToast({ title: r.reply || '指令已发送', icon: 'none' })
      this.refresh()
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' })
    }
  },

  // 真正的"睡觉/唤醒"：quiet_toggle 进入/退出静默模式（与主软件界面静默按钮一致）。
  // 静默时机器人不接收对话/不播报，直到再次切换唤醒。
  async toggleSleep() {
    if (this.data.sleepToggling) return
    this.setData({ sleepToggling: true })
    try {
      const r = await api.command('quiet_toggle')
      const nowSleeping = !(this.data.state && this.data.state.assistant_sleeping)
      wx.showToast({
        title: nowSleeping ? '已让小陪安静休息' : '已唤醒小陪',
        icon: 'none'
      })
      this.refresh()
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' })
    } finally {
      this.setData({ sleepToggling: false })
    }
  }
})
