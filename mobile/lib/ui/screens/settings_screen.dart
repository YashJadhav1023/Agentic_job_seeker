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

  Future<void> _uploadResume() async {
    final picked = await FilePicker.pickFiles(type: FileType.custom, allowedExtensions: ['pdf']);
    final file = picked.singleOrNull;
    if (file == null) return;
    try {
      final r = await requireClientFrom(ref).uploadResume(await file.readAsBytes(), file.name);
      _snack('Resume uploaded successfully (${(r['bytes'] / 1024).toStringAsFixed(1)} KB)');
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
        title: const Text('Gmail App Password'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Generate a 16-character App Password at myaccount.google.com → Security → App passwords.\n\n'
              'It will be securely saved on your server (file mode 0600) and never exposed.',
              style: TextStyle(fontSize: 13, color: AppTheme.textSecondary, height: 1.4),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: ctl,
              obscureText: true,
              autofocus: true,
              decoration: const InputDecoration(
                labelText: '16-character password',
                prefixIcon: Icon(Icons.key_rounded),
              ),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(c, ctl.text), child: const Text('Save Password')),
        ],
      ),
    );
    ctl.dispose();
    if (v == null || v.trim().isEmpty) return;
    try {
      await requireClientFrom(ref).setSecret('gmail_app_password', v.trim());
      _snack('Gmail password saved on server');
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
                    hintText: 'https://3-108-123-150.sslip.io',
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
                  title: const Text('Upload Resume (PDF)', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 14)),
                  subtitle: const Text('Attached to cold emails and used for job match scoring',
                      style: TextStyle(fontSize: 12, color: AppTheme.textSecondary)),
                  trailing: const Icon(Icons.upload_file_rounded, color: AppTheme.textSecondary, size: 20),
                  onTap: _uploadResume,
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
                const Divider(height: 1),
                ListTile(
                  enabled: connected,
                  leading: Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: const Color(0xFFF472B6).withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: const Icon(Icons.mail_lock_rounded, size: 20, color: Color(0xFFF472B6)),
                  ),
                  title: const Text('Gmail App Password', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 14)),
                  subtitle: Text(
                    creds['gmail_app_password'] == true ? 'Saved & ready on server' : 'Required for automated outreach',
                    style: TextStyle(
                      fontSize: 12,
                      color: creds['gmail_app_password'] == true ? AppTheme.successLight : AppTheme.textSecondary,
                    ),
                  ),
                  trailing: creds['gmail_app_password'] == true
                      ? const Icon(Icons.check_circle_rounded, color: AppTheme.success, size: 20)
                      : const Icon(Icons.add_circle_outline_rounded, color: AppTheme.primaryLight, size: 20),
                  onTap: _setGmail,
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
                const Text(
                  '• Dry-run simulation by default on all tools\n'
                  '• Daily hard caps: max 25 applications and 20 emails per day\n'
                  '• 60-day company email deduplication window\n'
                  '• Email outreach restricted to Mon–Sat, 09:00–19:00 IST\n'
                  '• Instant kill-switch toggle from Home screen',
                  style: TextStyle(fontSize: 12.5, color: AppTheme.textSecondary, height: 1.5),
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
