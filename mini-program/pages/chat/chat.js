const api = require('../../services/api')
Page({
  data: { messages: [{ role:'bot', text:'你好，我是小陪。想聊聊今天的心情，还是需要我陪你开始专注？' }], input:'', sending:false },
  input(e) { this.setData({ input: e.detail.value }) },
  async send() { const text = this.data.input.trim(); if (!text || this.data.sending) return; const messages = this.data.messages.concat({ role:'user', text }); this.setData({ messages, input:'', sending:true }); try { const res = await api.chat(text); this.setData({ messages: messages.concat({ role:'bot', text: res.reply || '我在听。' }) }) } catch(e) { this.setData({ messages: messages.concat({ role:'bot', text: '暂时没有连上小陪：' + e.message }) }) } finally { this.setData({ sending:false }) } },
  async speak(e) { try { await api.tts(e.currentTarget.dataset.text); wx.showToast({ title:'已让小陪播报', icon:'success' }) } catch(err) { wx.showToast({ title:err.message, icon:'none' }) } }
})
