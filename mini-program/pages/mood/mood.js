const api = require('../../services/api'); const moods=['开心','平静','疲惫','难过','焦虑']
Page({data:{moods,selected:'',reply:'情绪没有好坏，每一种都值得看见。'},async pick(e){const mood=e.currentTarget.dataset.mood;this.setData({selected:mood});try{const r=await api.mood(mood);this.setData({reply:r.reply||'我记住了，今天会更贴近你的节奏。'})}catch(err){wx.showToast({title:err.message,icon:'none'})}}})
