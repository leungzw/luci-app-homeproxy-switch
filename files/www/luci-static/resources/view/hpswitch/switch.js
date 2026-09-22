'use strict';
'require view';
'require rpc';

const callExec = rpc.declare({
	object: 'file',
	method: 'exec',
	params: [ 'command', 'params' ],
	expect: { '': { code: 0, stdout: '', stderr: '' } }
});

function readStatus() {
	return L.resolveDefault(callExec('/usr/bin/hpswitch', [ 'status' ]), null).then(function(res) {
		if (!res)
			return { mode: 'error', raw: _('ubus 调用失败：权限不足或超时（检查 rpcd ACL 里是否放行 /usr/bin/hpswitch 的 exec）') };

		var out = (res.stdout) ? String(res.stdout).trim() : '';

		if (res.code !== 0)
			return { mode: 'error', raw: _('脚本退出码 ') + res.code + '：' + (res.stderr || out) };

		try {
			var o = JSON.parse(out);
			return { running: !!o.running, boot: !!o.boot, mode: o.mode || 'unknown' };
		} catch (e) {
			return { mode: 'error', raw: _('返回内容无法解析：') + (out || '（空）') };
		}
	});
}

return view.extend({
	load: function() {
		return readStatus();
	},

	render: function(state) {
		function statusLine(st) {
			var color = '#d9534f', text;

			if (st.mode === 'full') {
				color = '#5cb85c';
				text = _('代理已开启：路由器 + 局域网设备（国内直连，国外走代理）');
			} else if (st.mode === 'self') {
				color = '#f0ad4e';
				text = _('仅路由器自身走代理，局域网设备不走');
			} else if (st.mode === 'off') {
				color = '#888';
				text = _('代理已关闭，所有流量走上级路由');
			} else if (st.mode === 'busy') {
				color = '#777';
				text = _('执行中，请稍候...');
			} else if (st.mode === 'error') {
				text = _('读取状态失败：') + (st.raw || '');
			} else {
				text = _('状态未知');
			}

			return E('div', {}, [
				E('div', { 'style': 'font-size:15px;font-weight:bold;color:%s'.format(color) }, [ text ]),
				E('div', { 'style': 'color:#777;font-size:12px;margin-top:4px' }, [
					_('sing-box 进程：') + (st.running ? _('运行中') : _('已停止')) + '　' +
					_('开机自启：') + (st.boot ? _('开') : _('关'))
				])
			]);
		}

		var stateEl = E('div', { 'class': 'cbi-value', 'style': 'padding:8px 0' });

		function repaint(st) {
			stateEl.innerHTML = '';
			stateEl.appendChild(statusLine(st));
		}

		function act(mode) {
			btnOn.disabled = btnSelf.disabled = btnOff.disabled = true;
			repaint({ running: false, boot: false, mode: 'busy' });

			callExec('/usr/bin/hpswitch', [ mode ]).then(function(res) {
				if (res && res.code !== 0)
					throw new Error(_('脚本退出码 ') + res.code + '：' + (res.stderr || res.stdout || ''));
				return readStatus();
			}).then(function(st) {
				repaint(st);
			}).catch(function(e) {
				stateEl.innerHTML = '';
				stateEl.appendChild(E('div', { 'style': 'color:#d9534f' }, [
					_('执行失败：') + ((e && e.message) ? e.message : String(e))
				]));
			}).then(function() {
				btnOn.disabled = btnSelf.disabled = btnOff.disabled = false;
			});
		}

		var btnOn = E('button', {
			'class': 'btn cbi-button cbi-button-apply',
			'click': function() { act('full'); }
		}, [ _('开启（路由器 + 局域网）') ]);

		var btnSelf = E('button', {
			'class': 'btn cbi-button cbi-button-reset',
			'click': function() { act('self'); }
		}, [ _('仅路由器自身') ]);

		var btnOff = E('button', {
			'class': 'btn cbi-button cbi-button-negative',
			'click': function() { act('off'); }
		}, [ _('关闭代理') ]);

		repaint(state || { running: false, boot: false, mode: 'unknown' });

		return E('div', { 'class': 'cbi-map' }, [
			E('h2', {}, [ _('HomeProxy 总开关') ]),
			E('div', { 'class': 'cbi-section' }, [
				stateEl,
				E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;margin-top:10px' }, [ btnOn, btnSelf, btnOff ]),
				E('div', { 'class': 'cbi-section-descr', 'style': 'margin-top:14px;line-height:1.6' }, [
					_('开关同时控制 sing-box 的运行状态和开机自启，无需再进「系统 - 启动项」。'),
					E('br'),
					_('关闭后局域网设备不再被透明代理，全部流量交给上级路由（上级本身也在代理，不会断网）。')
				])
			])
		]);
	}
});
