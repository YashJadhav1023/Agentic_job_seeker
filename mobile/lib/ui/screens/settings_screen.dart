import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../api/client.dart';
import '../../state/providers.dart';
import 'tool_run_screen.dart';

/// Server connection, profile editor, resume upload and Gmail app password.
class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  final _url = TextEditingController();
  final _token = TextEditingController();
  bool _obscure = true;
  bool _testing = false;
  String? _testMsg;
  bool _loaded = false;

  @override
  void dispose() {
    _url.dispose();
    _token.dispose();
    super.dispose();
  }

  void _snack(String m) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));

  /// Normalises the URL and refuses plain HTTP: the token and your mail password travel over this link.
  String? _validUrl(String raw) {
    var u = raw.trim();
    if (u.isEmpty) return null;
    if (!u.contains('://')) u = 'https://$u';
    final p = Uri.tryParse(u);
    if (p == null || p.scheme != 'https' || p.host.isEmpty) return null;
    return u.replaceAll(RegExp(r'/+$'), '');
  }

  Future<void> _saveAndTest() async {
    final url = _validUrl(_url.text);
    if (url == null) {
      setState(() => _testMsg = 'Enter an https:// server address (plain http is not allowed).');
      return;
    }
    if (_token.text.trim().length < 32) {
      setState(() => _testMsg = 'The API token is at least 32 characters (printed by deploy/setup.sh).');
      return;
    }
    setState(() {
      _testing = true;
      _testMsg = null;
    });
    final c = ApiClient(baseUrl: url, token: _token.text.trim());
    try {
      final h = await c.health();
      final tools = await c.tools(); // authenticated call proves the token works
      await ref.read(settingsProvider.notifier).save(ServerSettings(baseUrl: url, token: _token.text.trim()));
      ref
        ..invalidate(toolsProvider)
        ..invalidate(statusProvider)
        ..invalidate(runsProvider);
      setState(() => _testMsg = 'Connected · server v${h['version']} · ${tools.length} tools');
    } catch (e) {
      setState(() => _testMsg = 'Failed: $e');
    } finally {
      if (mounted) setState(() => _testing = false);
    }
  }

  Future<void> _uploadResume() async {
    final picked = await FilePicker.pickFiles(type: FileType.custom, allowedExtensions: ['pdf']);
    final file = picked.singleOrNull;
    if (file == null) return;
    try {
      final r = await requireClientFrom(ref).uploadResume(await file.readAsBytes(), file.name);
      _snack('Resume uploaded (${r['bytes']} bytes)');
      ref.invalidate(statusProvider);
    } catch (e) {
      _snack('$e');
    }
  }

  Future<void> _setGmail() async {
    final ctl = TextEditingController();
    final v = await showDialog<String>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Gmail app password'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          const Text('Create one at myaccount.google.com → Security → App passwords. '
              'It is stored on your server (file mode 600) and never shown again.'),
          const SizedBox(height: 12),
          TextField(controller: ctl, obscureText: true, autofocus: true, decoration: const InputDecoration(labelText: '16-character password')),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(c, ctl.text), child: const Text('Save')),
        ],
      ),
    );
    ctl.dispose();
    if (v == null || v.trim().isEmpty) return;
    try {
      await requireClientFrom(ref).setSecret('gmail_app_password', v.trim());
      _snack('Saved on server');
      ref.invalidate(statusProvider);
    } catch (e) {
      _snack('$e');
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = ref.watch(settingsProvider);
    if (!_loaded && s.hasValue) {
      _url.text = s.value!.baseUrl;
      _token.text = s.value!.token;
      _loaded = true;
    }
    final connected = ref.watch(clientProvider) != null;
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Text('Server', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 8),
        TextField(
          controller: _url,
          keyboardType: TextInputType.url,
          autocorrect: false,
          decoration: const InputDecoration(labelText: 'Server URL', hintText: 'https://jobs.example.com', prefixIcon: Icon(Icons.dns_outlined)),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _token,
          obscureText: _obscure,
          autocorrect: false,
          enableSuggestions: false,
          decoration: InputDecoration(
            labelText: 'API token',
            prefixIcon: const Icon(Icons.key),
            suffixIcon: IconButton(
              icon: Icon(_obscure ? Icons.visibility : Icons.visibility_off),
              onPressed: () => setState(() => _obscure = !_obscure),
            ),
          ),
        ),
        const SizedBox(height: 12),
        FilledButton.icon(
          onPressed: _testing ? null : _saveAndTest,
          icon: _testing
              ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
              : const Icon(Icons.link),
          label: const Text('Save & test connection'),
        ),
        if (_testMsg != null) Padding(padding: const EdgeInsets.only(top: 8), child: Text(_testMsg!)),
        const Divider(height: 40),
        Text('Your job profile', style: Theme.of(context).textTheme.titleMedium),
        ListTile(
          enabled: connected,
          leading: const Icon(Icons.badge_outlined),
          title: const Text('Edit profile'),
          subtitle: const Text('Name, keywords, cities, CTC, caps and safety switch'),
          onTap: () => context.push('/settings/profile'),
        ),
        ListTile(
          enabled: connected,
          leading: const Icon(Icons.picture_as_pdf_outlined),
          title: const Text('Upload resume (PDF)'),
          subtitle: const Text('Used for match scoring and attached to emails'),
          onTap: _uploadResume,
        ),
        ListTile(
          enabled: connected,
          leading: const Icon(Icons.fact_check_outlined),
          title: const Text('Check resume for ATS'),
          onTap: () => context.push('/tools/verify_resume_pdf'),
        ),
        ListTile(
          enabled: connected,
          leading: const Icon(Icons.mail_lock_outlined),
          title: const Text('Gmail app password'),
          subtitle: const Text('Needed to send applications by email'),
          onTap: _setGmail,
        ),
        const Divider(height: 40),
        const ListTile(
          leading: Icon(Icons.info_outline),
          title: Text('Agentic Job Seeker'),
          subtitle: Text('Everything that applies or sends email runs as a dry run unless you switch it off, '
              'and the server enforces daily caps, a one-email-per-company window and a kill switch.'),
        ),
      ]),
    );
  }
}

/// Raw YAML editor for profile.yaml; the server validates before saving.
class ProfileEditorScreen extends ConsumerStatefulWidget {
  const ProfileEditorScreen({super.key});

  @override
  ConsumerState<ProfileEditorScreen> createState() => _ProfileEditorScreenState();
}

class _ProfileEditorScreenState extends ConsumerState<ProfileEditorScreen> {
  final _ctl = TextEditingController();
  bool _loading = true;
  bool _saving = false;
  String? _msg;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _ctl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final p = await requireClientFrom(ref).profile();
      _ctl.text = '${p['yaml']}';
    } catch (e) {
      _msg = '$e';
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _msg = null;
    });
    try {
      final r = await requireClientFrom(ref).saveProfile(_ctl.text);
      final miss = (r['missing_profile_fields'] as List?) ?? const [];
      setState(() => _msg = miss.isEmpty ? 'Saved' : 'Saved · still missing: ${miss.join(', ')}');
      ref.invalidate(statusProvider);
    } catch (e) {
      setState(() => _msg = '$e');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Profile (YAML)'), actions: [
        TextButton.icon(onPressed: _saving || _loading ? null : _save, icon: const Icon(Icons.save), label: const Text('Save')),
      ]),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : Column(children: [
              if (_msg != null) MaterialBanner(content: Text(_msg!), actions: [
                TextButton(onPressed: () => setState(() => _msg = null), child: const Text('OK')),
              ]),
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: TextField(
                    controller: _ctl,
                    expands: true,
                    maxLines: null,
                    keyboardType: TextInputType.multiline,
                    style: const TextStyle(fontFamily: 'monospace', fontSize: 13),
                    decoration: const InputDecoration(border: OutlineInputBorder()),
                  ),
                ),
              ),
            ]),
    );
  }
}
