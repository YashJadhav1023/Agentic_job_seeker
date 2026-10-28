import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../api/client.dart';
import '../../state/providers.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'tool_run_screen.dart';

/// Modern Settings Screen: Server connection, candidate profile, resume upload, and credentials.
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
  bool _testSuccess = false;
  bool _loaded = false;

  @override
  void dispose() {
    _url.dispose();
    _token.dispose();
    super.dispose();
  }

  void _snack(String m) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));

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
      setState(() {
        _testMsg = 'Please enter a valid https:// server address.';
        _testSuccess = false;
      });
      return;
    }
    if (_token.text.trim().length < 32) {
      setState(() {
        _testMsg = 'The API token must be at least 32 characters.';
        _testSuccess = false;
      });
      return;
    }
    setState(() {
      _testing = true;
      _testMsg = null;
    });
    final c = ApiClient(baseUrl: url, token: _token.text.trim());
    try {
      final h = await c.health();
      final tools = await c.tools();
      await ref.read(settingsProvider.notifier).save(ServerSettings(baseUrl: url, token: _token.text.trim()));
      ref
        ..invalidate(toolsProvider)
        ..invalidate(statusProvider)
        ..invalidate(runsProvider);
      setState(() {
        _testSuccess = true;
        _testMsg = 'Connected to server v${h['version']} with ${tools.length} tools available.';
      });
    } catch (e) {
      setState(() {
        _testSuccess = false;
        _testMsg = 'Connection failed: $e';
      });
    } finally {
      if (mounted) setState(() => _testing = false);
    }
  }

  Future<void> _uploadResume({bool naukriLinked = false}) async {
    final picked = await FilePicker.pickFiles(type: FileType.custom, allowedExtensions: ['pdf']);
    final file = picked.singleOrNull;
    if (file == null || !mounted) return;
    var replaceSearch = true;
    var syncNaukri = naukriLinked;
    final go = await showDialog<bool>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, setD) => AlertDialog(
          title: const Text('Use this resume'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(file.name, style: const TextStyle(fontSize: 13, color: AppTheme.textSecondary)),
              const SizedBox(height: 8),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                value: replaceSearch,
                onChanged: (v) => setD(() => replaceSearch = v ?? false),
                title: const Text('Find jobs that match this resume', style: TextStyle(fontSize: 13.5)),
                subtitle: const Text('Job titles and title filters are taken from the resume (needs AI analysis set up). '
                    'Skills are always refreshed.', style: TextStyle(fontSize: 11.5)),
              ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                value: syncNaukri,
                onChanged: naukriLinked ? (v) => setD(() => syncNaukri = v ?? false) : null,
                title: const Text('Also replace my Naukri profile resume', style: TextStyle(fontSize: 13.5)),
                subtitle: Text(naukriLinked ? 'Naukri applies with the resume on your Naukri profile.' : 'Link Naukri first.',
                    style: const TextStyle(fontSize: 11.5)),
              ),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Upload')),
          ],
        ),
      ),
    );
    if (go != true) return;
    try {
      final r = await requireClientFrom(ref)
          .uploadResume(await file.readAsBytes(), file.name, replaceSearch: replaceSearch, syncNaukri: syncNaukri);
      final changed = ((r['profile_update'] as Map?)?['changed'] as Map?)?.keys.join(', ') ?? '';
      final sync = r['naukri_sync'] is Map ? ((r['naukri_sync'] as Map)['error'] ?? 'Naukri update started') : null;
      _snack('Resume uploaded (${(r['bytes'] / 1024).toStringAsFixed(1)} KB)'
          '${changed.isEmpty ? '' : ' · profile updated: $changed'}${sync == null ? '' : ' · $sync'}');
      ref.invalidate(statusProvider);
    } catch (e) {
      _snack('$e');
    }
  }

  Future<void> _setSecret(String name, String title, String help) async {
    final ctl = TextEditingController();
    final v = await showDialog<String>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(title),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(help, style: const TextStyle(fontSize: 13, color: AppTheme.textSecondary, height: 1.4)),
            const SizedBox(height: 12),
            TextField(controller: ctl, obscureText: true, autofocus: true,
                decoration: const InputDecoration(labelText: 'Value', prefixIcon: Icon(Icons.key_rounded))),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(c, ctl.text), child: const Text('Save')),
        ],
      ),
    );
    ctl.dispose();
    if (v == null || v.trim().isEmpty) return;
    try {
      await requireClientFrom(ref).setSecret(name, v.trim());
      _snack('$title saved on the server');
      ref.invalidate(statusProvider);
    } catch (e) {
      _snack('$e');
    }
  }

  Future<void> _unlink(String platform) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text('Disconnect $platform?'),
        content: const Text('The saved login session is deleted from your server. You can link it again any time.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Disconnect')),
        ],
      ),
    );
    if (ok != true) return;
    final r = await requireClientFrom(ref).call('unlink_platform', {'platform': platform});
    _snack(r.isError ? (r.error ?? 'failed') : '$platform disconnected');
    ref.invalidate(statusProvider);
  }

  Future<void> _setGmail(String currentUser) async {
    final mail = TextEditingController(text: currentUser.contains('example') ? '' : currentUser);
    final pw = TextEditingController();
    final v = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Link Gmail'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Applications and HR emails are sent from this Gmail account with your resume attached.\n'
              'Create a 16-character App Password at myaccount.google.com → Security → App passwords '
              '(2-step verification must be on). It is stored on your server only and never shown again.',
              style: TextStyle(fontSize: 12.5, color: AppTheme.textSecondary, height: 1.4),
            ),
            const SizedBox(height: 12),
            TextField(controller: mail, keyboardType: TextInputType.emailAddress,
                decoration: const InputDecoration(labelText: 'Gmail address', prefixIcon: Icon(Icons.alternate_email_rounded))),
            const SizedBox(height: 10),
            TextField(controller: pw, obscureText: true,
                decoration: const InputDecoration(labelText: 'App password (16 characters)', prefixIcon: Icon(Icons.key_rounded))),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Test & Save')),
        ],
      ),
    );
    final email = mail.text.trim(), pass = pw.text.trim();
    mail.dispose();
    pw.dispose();
    if (v != true || email.isEmpty) return;
    try {
      await requireClientFrom(ref).linkGmail(email, pass);
      _snack(pass.isEmpty ? 'Gmail address saved' : 'Gmail linked and login tested');
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
    final status = ref.watch(statusProvider).value;
    final creds = ((status?['credentials'] as Map?) ?? const {}).cast<String, dynamic>();
    final sessions = ((status?['login_sessions_saved'] as Map?) ?? const {}).cast<String, dynamic>();
    final res = ((status?['resume'] as Map?) ?? const {}).cast<String, dynamic>();
    final llm = ((status?['llm'] as Map?) ?? const {}).cast<String, dynamic>();
    final runs = ((status?['runs'] as Map?) ?? const {}).cast<String, dynamic>();
    final gmailUser = '${creds['gmail_user'] ?? ''}';
    final resumeText = res['exists'] == true
        ? '${res['original_filename'] ?? res['attachment_name']} · ${(((res['bytes'] as num?) ?? 0) / 1024).toStringAsFixed(0)} KB'
            '${res['uploaded_at'] != null ? ' · uploaded ${'${res['uploaded_at']}'.substring(0, 16).replaceFirst('T', ' ')}' : ''}'
            '\nUsed for job search, scoring, emails and applications'
        : 'No resume on the server yet. Upload one to start.';

    return Scaffold(
      appBar: AppBar(title: const Text('Settings & Configuration')),
      body: ListView(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        children: [
          // Connection Status Card
          Container(
            decoration: BoxDecoration(
              color: AppTheme.card,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(
                color: connected ? AppTheme.success.withValues(alpha: 0.3) : AppTheme.borderSubtle,
              ),
            ),
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Container(
                      padding: const EdgeInsets.all(8),
                      decoration: BoxDecoration(
                        color: connected ? AppTheme.success.withValues(alpha: 0.12) : AppTheme.surface,
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: Icon(
                        connected ? Icons.cloud_done_rounded : Icons.cloud_off_rounded,
                        size: 20,
                        color: connected ? AppTheme.successLight : AppTheme.textMuted,
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            connected ? 'Server Connected' : 'Server Disconnected',
                            style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: AppTheme.textPrimary),
                          ),
                          Text(
                            connected ? 'HTTPS · Ready for background automation' : 'Enter URL and token below',
                            style: TextStyle(
                              fontSize: 12,
                              color: connected ? AppTheme.successLight : AppTheme.textMuted,
                            ),
                          ),
                        ],
                      ),
                    ),
                    StatusChip(connected ? 'ONLINE' : 'OFFLINE', isMini: true),
                  ],
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _url,
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                  decoration: const InputDecoration(
                    labelText: 'Server URL',
                    hintText: 'https://your-server.example.com',
                    prefixIcon: Icon(Icons.dns_rounded),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _token,
                  obscureText: _obscure,
                  autocorrect: false,
                  enableSuggestions: false,
                  decoration: InputDecoration(
                    labelText: 'API Token',
                    prefixIcon: const Icon(Icons.key_rounded),
                    suffixIcon: IconButton(
                      icon: Icon(_obscure ? Icons.visibility_rounded : Icons.visibility_off_rounded),
                      onPressed: () => setState(() => _obscure = !_obscure),
                    ),
                  ),
                ),
                const SizedBox(height: 14),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    onPressed: _testing ? null : _saveAndTest,
                    icon: _testing
                        ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                        : const Icon(Icons.link_rounded, size: 18),
                    label: Text(_testing ? 'Connecting...' : 'Save & Test Connection'),
                  ),
                ),
                if (_testMsg != null) ...[
                  const SizedBox(height: 10),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    decoration: BoxDecoration(
                      color: _testSuccess ? AppTheme.success.withValues(alpha: 0.12) : AppTheme.error.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(
                        color: _testSuccess ? AppTheme.success.withValues(alpha: 0.3) : AppTheme.error.withValues(alpha: 0.3),
                      ),
                    ),
                    child: Row(
                      children: [
                        Icon(
                          _testSuccess ? Icons.check_circle_rounded : Icons.error_outline_rounded,
                          size: 16,
                          color: _testSuccess ? AppTheme.successLight : AppTheme.error,
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            _testMsg!,
                            style: TextStyle(
                              fontSize: 12,
                              color: _testSuccess ? AppTheme.successLight : const Color(0xFFFCA5A5),
                              fontWeight: FontWeight.w500,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 18),
          // Job Profile Section
          const SectionHeader(title: 'Candidate Profile & Assets', icon: Icons.person_outline_rounded),
          Container(
            decoration: BoxDecoration(
              color: AppTheme.card,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: AppTheme.borderSubtle),
            ),
            child: Column(
              children: [
                ListTile(
                  enabled: connected,
                  leading: Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: AppTheme.primary.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: const Icon(Icons.badge_rounded, size: 20, color: AppTheme.primaryLight),
                  ),
                  title: const Text('Edit Job Profile', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 14)),
                  subtitle: const Text('Target cities, keywords, compensation, notice period & safety toggle',
                      style: TextStyle(fontSize: 12, color: AppTheme.textSecondary)),
                  trailing: const Icon(Icons.chevron_right_rounded, color: AppTheme.textMuted),
                  onTap: () => context.push('/settings/profile'),
                ),
                const Divider(height: 1),
                ListTile(
                  enabled: connected,
                  leading: Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: const Color(0xFF38BDF8).withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: const Icon(Icons.picture_as_pdf_rounded, size: 20, color: Color(0xFF38BDF8)),
                  ),
                  title: Text(res['exists'] == true ? 'Resume (replace)' : 'Upload Resume (PDF)',
                      style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14)),
                  subtitle: Text(resumeText, style: const TextStyle(fontSize: 12, color: AppTheme.textSecondary)),
                  isThreeLine: res['exists'] == true,
                  trailing: const Icon(Icons.upload_file_rounded, color: AppTheme.textSecondary, size: 20),
                  onTap: () => _uploadResume(naukriLinked: sessions['naukri'] == true),
                ),
                const Divider(height: 1),
                ListTile(
                  enabled: connected,
                  leading: Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: AppTheme.success.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: const Icon(Icons.fact_check_rounded, size: 20, color: AppTheme.successLight),
                  ),
                  title: const Text('Check Resume for ATS', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 14)),
                  subtitle: const Text('Extract and audit text layer, font encoding, and contact info',
                      style: TextStyle(fontSize: 12, color: AppTheme.textSecondary)),
                  trailing: const Icon(Icons.chevron_right_rounded, color: AppTheme.textMuted),
                  onTap: () => context.push('/tools/verify_resume_pdf'),
                ),
              ],
            ),
          ),
          const SizedBox(height: 18),
          const SectionHeader(title: 'Linked Accounts', icon: Icons.link_rounded),
          Container(
            decoration: BoxDecoration(
              color: AppTheme.card,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: AppTheme.borderSubtle),
            ),
            child: Column(
              children: [
                for (final (id, label, icon) in const [
                  ('linkedin', 'LinkedIn', Icons.business_center_rounded),
                  ('naukri', 'Naukri', Icons.work_rounded),
                  ('indeed', 'Indeed', Icons.search_rounded),
                ]) ...[
                  ListTile(
                    enabled: connected,
                    leading: Icon(icon, color: sessions[id] == true ? AppTheme.successLight : AppTheme.textMuted),
                    title: Text(label, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14)),
                    subtitle: Text(
                      sessions[id] == true ? 'Linked · session saved on your server' : 'Not linked · sign in once in the remote browser',
                      style: const TextStyle(fontSize: 12, color: AppTheme.textSecondary),
                    ),
                    trailing: sessions[id] == true
                        ? TextButton(onPressed: () => _unlink(id), child: const Text('Disconnect'))
                        : FilledButton.tonal(
                            onPressed: () => context.push('/tools/login_platform', extra: {'platform': id}),
                            child: const Text('Link')),
                  ),
                  const Divider(height: 1),
                ],
                ListTile(
                  enabled: connected,
                  leading: Icon(Icons.mail_lock_rounded,
                      color: creds['gmail_app_password'] == true ? AppTheme.successLight : AppTheme.textMuted),
                  title: const Text('Gmail', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 14)),
                  subtitle: Text(
                    creds['gmail_app_password'] == true && gmailUser.isNotEmpty
                        ? 'Linked · $gmailUser'
                        : 'Not linked · emails and applications are sent from here',
                    style: const TextStyle(fontSize: 12, color: AppTheme.textSecondary),
                  ),
                  trailing: FilledButton.tonal(
                      onPressed: () => _setGmail(gmailUser),
                      child: Text(creds['gmail_app_password'] == true ? 'Change' : 'Link')),
                ),
              ],
            ),
          ),
          const SizedBox(height: 18),
          const SectionHeader(title: 'AI Post Analysis & Alerts', icon: Icons.auto_awesome_rounded),
          Container(
            decoration: BoxDecoration(
              color: AppTheme.card,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: AppTheme.borderSubtle),
            ),
            child: Column(
              children: [
                ListTile(
                  enabled: connected,
                  leading: Icon(Icons.psychology_rounded,
                      color: llm['ready'] == true ? AppTheme.successLight : AppTheme.textMuted),
                  title: Text('AI provider: ${llm['provider'] ?? 'none'}',
                      style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14)),
                  subtitle: Text(
                    llm['ready'] == true
                        ? 'Ready · hiring posts and your resume are analysed by AI'
                        : '${llm['reason'] ?? 'Not set'} · set llm.provider in the profile',
                    style: const TextStyle(fontSize: 12, color: AppTheme.textSecondary),
                  ),
                ),
                const Divider(height: 1),
                ListTile(
                  enabled: connected,
                  leading: const Icon(Icons.vpn_key_rounded, color: AppTheme.primaryLight),
                  title: const Text('Kiro API key', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 14)),
                  subtitle: Text(creds['kiro_api_key'] == true ? 'Saved on server' : 'For llm.provider: kiro (Kiro Pro and above)',
                      style: const TextStyle(fontSize: 12, color: AppTheme.textSecondary)),
                  onTap: () => _setSecret('kiro_api_key', 'Kiro API key',
                      'Generate an API key in your Kiro account settings. Used only to analyse posts and your resume.'),
                ),
                const Divider(height: 1),
                ListTile(
                  enabled: connected,
                  leading: const Icon(Icons.key_rounded, color: AppTheme.primaryLight),
                  title: const Text('Other AI API key', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 14)),
                  subtitle: Text(creds['llm_api_key'] == true ? 'Saved on server' : 'For llm.provider: openai (DeepSeek, OpenRouter ...)',
                      style: const TextStyle(fontSize: 12, color: AppTheme.textSecondary)),
                  onTap: () => _setSecret('llm_api_key', 'AI API key', 'Key for the OpenAI-compatible API set in llm.base_url.'),
                ),
                const Divider(height: 1),
                ListTile(
                  enabled: connected,
                  leading: const Icon(Icons.notifications_active_rounded, color: AppTheme.primaryLight),
                  title: const Text('Telegram alerts', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 14)),
                  subtitle: Text(
                      creds['telegram_bot_token'] == true
                          ? 'Bot token saved · set notifications.telegram_chat_id in the profile'
                          : 'Optional: message per matching hiring post',
                      style: const TextStyle(fontSize: 12, color: AppTheme.textSecondary)),
                  onTap: () => _setSecret('telegram_bot_token', 'Telegram bot token',
                      'Create a bot with @BotFather and paste its token. Put your chat id in notifications.telegram_chat_id.'),
                ),
              ],
            ),
          ),
          const SizedBox(height: 18),
          // Safety & Guardrails Card
          Container(
            decoration: BoxDecoration(
              color: AppTheme.card,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: AppTheme.borderSubtle),
            ),
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.shield_outlined, size: 18, color: AppTheme.primaryLight),
                    const SizedBox(width: 8),
                    const Text('Safety & Operational Policy',
                        style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: AppTheme.textPrimary)),
                  ],
                ),
                const SizedBox(height: 8),
                Text(
                  '• Dry-run simulation by default on all tools\n'
                  '• Daily caps on applications and emails (set in the profile)\n'
                  '• One email per company per 60 days, opt-outs and bounces respected\n'
                  '• Email send window: ${status?['send_window'] ?? 'any time, every day'}\n'
                  '• Up to ${runs['max_parallel'] ?? 4} tasks run at the same time\n'
                  '• Instant kill-switch toggle from Home screen',
                  style: const TextStyle(fontSize: 12.5, color: AppTheme.textSecondary, height: 1.5),
                ),
              ],
            ),
          ),
          const SizedBox(height: 24),
        ],
      ),
    );
  }
}

/// Profile YAML editor with syntax styling and save confirmation.
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
  bool _isError = false;

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
      _isError = true;
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _msg = null;
      _isError = false;
    });
    try {
      final r = await requireClientFrom(ref).saveProfile(_ctl.text);
      final miss = (r['missing_profile_fields'] as List?) ?? const [];
      setState(() {
        _isError = false;
        _msg = miss.isEmpty ? 'Profile saved successfully!' : 'Saved · missing fields: ${miss.join(', ')}';
      });
      ref.invalidate(statusProvider);
    } catch (e) {
      setState(() {
        _isError = true;
        _msg = '$e';
      });
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Edit Job Profile (YAML)'),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: FilledButton.icon(
              onPressed: _saving || _loading ? null : _save,
              icon: _saving
                  ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Icon(Icons.save_rounded, size: 18),
              label: Text(_saving ? 'Saving...' : 'Save Profile'),
            ),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: AppTheme.primaryLight))
          : Column(
              children: [
                if (_msg != null)
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                    color: _isError ? AppTheme.error.withValues(alpha: 0.15) : AppTheme.success.withValues(alpha: 0.15),
                    child: Row(
                      children: [
                        Icon(
                          _isError ? Icons.error_outline_rounded : Icons.check_circle_rounded,
                          size: 18,
                          color: _isError ? AppTheme.error : AppTheme.successLight,
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            _msg!,
                            style: TextStyle(
                              color: _isError ? const Color(0xFFFCA5A5) : AppTheme.successLight,
                              fontSize: 13,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                        IconButton(
                          icon: const Icon(Icons.close_rounded, size: 16),
                          visualDensity: VisualDensity.compact,
                          onPressed: () => setState(() => _msg = null),
                        ),
                      ],
                    ),
                  ),
                Expanded(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Container(
                      decoration: BoxDecoration(
                        color: AppTheme.surface,
                        borderRadius: BorderRadius.circular(16),
                        border: Border.all(color: AppTheme.borderSubtle),
                      ),
                      padding: const EdgeInsets.all(12),
                      child: TextField(
                        controller: _ctl,
                        expands: true,
                        maxLines: null,
                        keyboardType: TextInputType.multiline,
                        style: const TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 12.5,
                          height: 1.45,
                          color: AppTheme.textPrimary,
                        ),
                        decoration: const InputDecoration(
                          border: InputBorder.none,
                          enabledBorder: InputBorder.none,
                          focusedBorder: InputBorder.none,
                          fillColor: Colors.transparent,
                        ),
                      ),
                    ),
                  ),
                ),
              ],
            ),
    );
  }
}
