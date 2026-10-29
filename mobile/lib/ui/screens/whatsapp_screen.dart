import 'dart:async';
import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/client.dart';
import '../../state/providers.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'tool_run_screen.dart' show requireClientFrom;

final whatsappProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) async {
  final r = await requireClient(ref).call('whatsapp_status');
  if (r.isError || r.result is! Map) throw ApiException(r.error ?? 'whatsapp_status failed');
  return (r.result as Map).cast<String, dynamic>();
});

/// WhatsApp: link the number (QR), upload a list of numbers (PDF / Word / Excel / CSV), set the message, preview, send.
class WhatsAppScreen extends ConsumerStatefulWidget {
  const WhatsAppScreen({super.key});

  @override
  ConsumerState<WhatsAppScreen> createState() => _WhatsAppScreenState();
}

class _WhatsAppScreenState extends ConsumerState<WhatsAppScreen> {
  String? _qr;
  String? _code;
  String _phone = '';
  String _linkNote = '';
  bool _linking = false;
  Timer? _poll;
  bool _busy = false;
  Map<String, dynamic>? _preview;
  final _template = TextEditingController();
  bool _templateLoaded = false;

  @override
  void dispose() {
    _poll?.cancel();
    _template.dispose();
    super.dispose();
  }

  void _snack(String m) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));
  }

  Future<Map<String, dynamic>> _tool(String name, [Map<String, dynamic> args = const {}]) async {
    final r = await requireClientFrom(ref).call(name, args);
    final res = r.result is Map ? (r.result as Map).cast<String, dynamic>() : <String, dynamic>{};
    if (r.isError) throw ApiException(r.error ?? '$name failed');
    return res;
  }

  Future<void> _link() async {
    setState(() {
      _linking = true;
      _linkNote = 'Starting…';
    });
    _poll?.cancel();
    Future<void> step() async {
      try {
        final r = await _tool('whatsapp_link');
        if (!mounted) return;
        if (r['state'] == 'linked') {
          _poll?.cancel();
          ref.invalidate(whatsappProvider);
          setState(() {
            _linking = false;
            _qr = null;
            _linkNote = 'Linked as ${r['name'] ?? 'your number'}.';
          });
          return;
        }
        setState(() {
          _qr = (r['qr'] as String?)?.isNotEmpty == true ? r['qr'] as String : _qr;
          _linkNote = '${r['note'] ?? ''}';
        });
      } catch (e) {
        _poll?.cancel();
        if (mounted) {
          setState(() {
            _linking = false;
            _linkNote = '$e';
          });
        }
      }
    }

    await step();
    if (_linking) _poll = Timer.periodic(const Duration(seconds: 4), (_) => step());
  }

  /// Same-phone linking: WhatsApp cannot scan a QR shown on its own screen, so ask for a pairing code instead.
  Future<void> _linkWithCode() async {
    final ctl = TextEditingController(text: _phone);
    final phone = await showDialog<String>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Link with phone number'),
        content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text('Use this when WhatsApp is on this same phone. Enter the WhatsApp number to link, with country code.',
              style: TextStyle(fontSize: 13)),
          const SizedBox(height: 12),
          TextField(
            controller: ctl,
            autofocus: true,
            keyboardType: TextInputType.phone,
            decoration: const InputDecoration(labelText: 'WhatsApp number', hintText: '+91 98765 43210'),
          ),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(c, ctl.text.trim()), child: const Text('Get code')),
        ],
      ),
    );
    if (phone == null || phone.isEmpty || !mounted) return;
    _poll?.cancel();
    setState(() {
      _phone = phone;
      _linking = true;
      _qr = null;
      _code = null;
      _linkNote = 'Asking WhatsApp for a code…';
    });
    try {
      final r = await _tool('whatsapp_link', {'phone': phone});
      if (!mounted) return;
      if (r['state'] == 'linked') {
        ref.invalidate(whatsappProvider);
        setState(() {
          _linking = false;
          _linkNote = 'Linked as ${r['name'] ?? 'your number'}.';
        });
        return;
      }
      if (r['error'] != null) throw ApiException('${r['error']}');
      setState(() {
        _code = '${r['code']}';
        _linkNote = '${r['note'] ?? ''}';
      });
      // wait for the user to type the code in WhatsApp
      var waited = 0;
      _poll = Timer.periodic(const Duration(seconds: 4), (t) async {
        waited += 4;
        try {
          final s = await _tool('whatsapp_status');
          if (!mounted) return;
          if (s['linked'] == true) {
            t.cancel();
            ref.invalidate(whatsappProvider);
            setState(() {
              _linking = false;
              _code = null;
              _linkNote = 'Linked as ${s['name'] ?? 'your number'}.';
            });
          } else if (waited >= 180) {
            t.cancel();
            setState(() {
              _linking = false;
              _code = null;
              _linkNote = 'The code expired. Tap "Link with phone number" for a new one.';
            });
          }
        } catch (_) {}
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _linking = false;
          _linkNote = '$e';
        });
      }
    }
  }

  Future<void> _upload() async {
    final picked = await FilePicker.pickFiles(type: FileType.custom, allowedExtensions: ['pdf', 'docx', 'xlsx', 'csv', 'txt']);
    final file = picked.singleOrNull;
    if (file == null || !mounted) return;
    setState(() => _busy = true);
    try {
      final r = await requireClientFrom(ref).uploadWhatsappNumbers(await file.readAsBytes(), file.name);
      _snack('${r['found']} numbers found: ${r['new']} new, ${r['already_known']} already in the list');
      ref.invalidate(whatsappProvider);
    } catch (e) {
      _snack('$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _saveSettings(Map<String, dynamic> patch) async {
    try {
      await _tool('whatsapp_settings', patch);
      ref.invalidate(whatsappProvider);
    } catch (e) {
      _snack('$e');
    }
  }

  Future<void> _send({required bool live}) async {
    if (live) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          icon: const Icon(Icons.warning_amber_rounded),
          title: const Text('Send real WhatsApp messages?'),
          content: const Text('Each new number gets your message with your resume attached, with pauses between messages and your daily cap. '
              'Unwanted messages can get a WhatsApp number restricted, so only send to people who expect to hear from you.'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Send')),
          ],
        ),
      );
      if (ok != true) return;
    }
    setState(() => _busy = true);
    try {
      final r = await _tool('whatsapp_send', {'dry_run': !live, 'limit': 10});
      if (!live) {
        setState(() => _preview = r);
      } else {
        _snack('${r['note'] ?? 'Started'}');
        ref.invalidate(runsProvider);
        ref.invalidate(queueProvider);
      }
      ref.invalidate(whatsappProvider);
    } catch (e) {
      _snack('$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final st = ref.watch(whatsappProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('WhatsApp'),
        actions: [IconButton(icon: const Icon(Icons.refresh_rounded), onPressed: () => ref.invalidate(whatsappProvider))],
      ),
      body: AsyncBody<Map<String, dynamic>>(
        value: st,
        onRetry: () => ref.invalidate(whatsappProvider),
        data: (s) {
          final settings = ((s['settings'] as Map?) ?? const {}).cast<String, dynamic>();
          if (!_templateLoaded) {
            _template.text = '${settings['template'] ?? ''}';
            _templateLoaded = true;
          }
          final linked = s['linked'] == true;
          final configured = s['configured'] == true;
          final counts = ((s['contacts'] as Map?) ?? const {}).cast<String, dynamic>();
          final cap = s['daily_cap'] ?? 20;
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(14),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Row(children: [
                      Icon(linked ? Icons.check_circle_rounded : Icons.link_off_rounded,
                          color: linked ? AppTheme.success : AppTheme.textMuted),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Text(
                          !configured
                              ? 'Gateway not running'
                              : linked
                                  ? 'Linked${(s['name'] ?? '').toString().isNotEmpty ? ' as ${s['name']}' : ''}'
                                  : 'Not linked',
                          style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15),
                        ),
                      ),
                      if (configured && !linked)
                        FilledButton(onPressed: _linking ? null : _link, child: const Text('Link WhatsApp')),
                      if (linked)
                        TextButton(
                          onPressed: () async {
                            await _tool('whatsapp_unlink');
                            ref.invalidate(whatsappProvider);
                          },
                          child: const Text('Unlink'),
                        ),
                    ]),
                    if (!configured)
                      Padding(
                        padding: const EdgeInsets.only(top: 8),
                        child: Text('${s['hint'] ?? ''}', style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12.5)),
                      ),
                    if (s['gateway_error'] != null)
                      Padding(
                        padding: const EdgeInsets.only(top: 8),
                        child: Text('${s['gateway_error']}. If it says 503, finish the one-time license registration in the gateway Manager page.',
                            style: const TextStyle(color: AppTheme.warningLight, fontSize: 12.5)),
                      ),
                    if (configured && !linked)
                      Padding(
                        padding: const EdgeInsets.only(top: 8),
                        child: Align(
                          alignment: Alignment.centerLeft,
                          child: TextButton.icon(
                            onPressed: _linking && _code != null ? null : _linkWithCode,
                            icon: const Icon(Icons.dialpad_rounded, size: 18),
                            label: const Text('WhatsApp on this phone? Link with phone number'),
                          ),
                        ),
                      ),
                    if (_code != null)
                      Center(
                        child: Padding(
                          padding: const EdgeInsets.only(top: 12),
                          child: Column(children: [
                            SelectableText(_code!,
                                style: const TextStyle(fontSize: 34, fontWeight: FontWeight.w800, letterSpacing: 4, fontFamily: 'monospace')),
                            TextButton.icon(
                              onPressed: () {
                                Clipboard.setData(ClipboardData(text: _code!.replaceAll('-', '')));
                                _snack('Code copied');
                              },
                              icon: const Icon(Icons.copy_rounded, size: 18),
                              label: const Text('Copy code'),
                            ),
                          ]),
                        ),
                      ),
                    if (_qr != null)
                      Center(
                        child: Container(
                          margin: const EdgeInsets.only(top: 12),
                          color: Colors.white,
                          padding: const EdgeInsets.all(8),
                          child: Image.memory(base64Decode(_qr!.split(',').last), width: 240, height: 240, gaplessPlayback: true),
                        ),
                      ),
                    if (_linkNote.isNotEmpty)
                      Padding(padding: const EdgeInsets.only(top: 8), child: Text(_linkNote, style: const TextStyle(fontSize: 12.5))),
                  ]),
                ),
              ),
              const SizedBox(height: 8),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(14),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    const Text('Numbers', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
                    const SizedBox(height: 6),
                    Wrap(spacing: 6, children: [
                      for (final e in counts.entries) Chip(visualDensity: VisualDensity.compact, label: Text('${e.key}  ${e.value}')),
                      if (counts.isEmpty) const Text('No numbers yet.', style: TextStyle(color: AppTheme.textSecondary)),
                    ]),
                    const SizedBox(height: 8),
                    OutlinedButton.icon(
                      onPressed: _busy ? null : _upload,
                      icon: const Icon(Icons.upload_file_rounded),
                      label: const Text('Upload list (PDF, Word, Excel, CSV)'),
                    ),
                  ]),
                ),
              ),
              const SizedBox(height: 8),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(14),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    const Text('Message', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
                    const SizedBox(height: 6),
                    TextField(
                      controller: _template,
                      minLines: 3,
                      maxLines: 6,
                      decoration: const InputDecoration(helperText: 'Use {name} {candidate} {role} {company}. Your resume is attached and a "reply STOP" line is added.'),
                    ),
                    Align(
                      alignment: Alignment.centerRight,
                      child: TextButton(onPressed: () => _saveSettings({'template': _template.text}), child: const Text('Save message')),
                    ),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      title: const Text('These people expect to hear from me'),
                      subtitle: const Text('Required before any real send. Do not message people who never shared their number for this.'),
                      value: settings['recipients_expect_my_message'] == true,
                      onChanged: (v) => _saveSettings({'recipients_expect_my_message': v}),
                    ),
                    Row(children: [
                      Expanded(child: Text('Daily cap: $cap (max 50) · sent today ${s['sent_today'] ?? 0}')),
                      IconButton(
                        icon: const Icon(Icons.remove_circle_outline),
                        onPressed: () => _saveSettings({'daily_cap': (cap as int) - 5 < 1 ? 1 : cap - 5}),
                      ),
                      IconButton(icon: const Icon(Icons.add_circle_outline), onPressed: () => _saveSettings({'daily_cap': (cap as int) + 5})),
                    ]),
                    const SizedBox(height: 4),
                    Row(children: [
                      Expanded(
                        child: OutlinedButton.icon(
                          onPressed: _busy ? null : () => _send(live: false),
                          icon: const Icon(Icons.visibility_rounded),
                          label: const Text('Preview'),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: FilledButton.icon(
                          onPressed: _busy || !linked ? null : () => _send(live: true),
                          icon: const Icon(Icons.send_rounded),
                          label: const Text('Send'),
                        ),
                      ),
                    ]),
                  ]),
                ),
              ),
              if (_preview != null) ...[
                const SizedBox(height: 8),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(14),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text('Preview: ${_preview!['would_send']} would be sent (${_preview!['remaining_today']} left today)',
                          style: const TextStyle(fontWeight: FontWeight.w700)),
                      for (final d in ((_preview!['drafts'] as List?) ?? const []).cast<Map<String, dynamic>>())
                        Padding(
                          padding: const EdgeInsets.only(top: 10),
                          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Text('To +${d['to']}${(d['name'] ?? '').toString().isEmpty ? '' : '  (${d['name']})'}',
                                style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12)),
                            Text('${d['message']}'),
                          ]),
                        ),
                    ]),
                  ),
                ),
              ],
            ],
          );
        },
      ),
    );
  }
}
