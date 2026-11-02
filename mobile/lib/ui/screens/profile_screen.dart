import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/providers.dart';
import '../theme.dart';
import 'tool_run_screen.dart' show requireClientFrom;

/// Modern collapsible accordion Profile Editor supporting structured compensation,
/// experience, ATS facts, demographics, and screening Q&A.
class ProfileEditorScreen extends ConsumerStatefulWidget {
  const ProfileEditorScreen({super.key});

  @override
  ConsumerState<ProfileEditorScreen> createState() => _ProfileEditorScreenState();
}

class _ProfileEditorScreenState extends ConsumerState<ProfileEditorScreen> {
  bool _loading = true;
  bool _saving = false;
  String? _msg;
  bool _isError = false;

  // Raw profile data
  Map<String, dynamic> _profile = {};
  String _rawYaml = '';

  // Controllers: Compensation & Notice
  final _currentCtcCtrl = TextEditingController();
  final _expectedCtcCtrl = TextEditingController();
  final _minCtcCtrl = TextEditingController();
  final _noticeDaysCtrl = TextEditingController();
  String _servingNotice = 'No';
  final _lastWorkingDayCtrl = TextEditingController();

  // Controllers: Role & Experience
  final _currentTitleCtrl = TextEditingController();
  final _currentCompanyCtrl = TextEditingController();
  final _experienceYearsCtrl = TextEditingController();
  final _headlineCtrl = TextEditingController();
  final _keywordsCtrl = TextEditingController();
  final _skillsCtrl = TextEditingController();

  // Controllers: Location & Contact
  final _nameCtrl = TextEditingController();
  final _emailCtrl = TextEditingController();
  final _phoneCtrl = TextEditingController();
  final _cityCtrl = TextEditingController();
  final _stateCtrl = TextEditingController();
  final _countryCtrl = TextEditingController();
  final _postalCodeCtrl = TextEditingController();
  String _willingToRelocate = 'Yes';
  final _prefLocationsCtrl = TextEditingController();

  // Controllers: Education & Work Eligibility
  final _degreeCtrl = TextEditingController();
  final _branchCtrl = TextEditingController();
  final _universityCtrl = TextEditingController();
  final _gradYearCtrl = TextEditingController();
  final _gpaCtrl = TextEditingController();
  String _workAuth = 'Yes';
  String _sponsorship = 'No';
  String _gender = 'Prefer not to say';
  String _disability = 'Prefer not to say';
  String _veteran = 'No';

  // Controllers: Links
  final _linkedinCtrl = TextEditingController();
  final _githubCtrl = TextEditingController();
  final _portfolioCtrl = TextEditingController();
  final _coverNoteCtrl = TextEditingController();

  // YAML editor controller
  final _yamlCtrl = TextEditingController();

  // Form QA questions
  List<dynamic> _qaList = [];
  bool _loadingQa = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _currentCtcCtrl.dispose();
    _expectedCtcCtrl.dispose();
    _minCtcCtrl.dispose();
    _noticeDaysCtrl.dispose();
    _lastWorkingDayCtrl.dispose();
    _currentTitleCtrl.dispose();
    _currentCompanyCtrl.dispose();
    _experienceYearsCtrl.dispose();
    _headlineCtrl.dispose();
    _keywordsCtrl.dispose();
    _skillsCtrl.dispose();
    _nameCtrl.dispose();
    _emailCtrl.dispose();
    _phoneCtrl.dispose();
    _cityCtrl.dispose();
    _stateCtrl.dispose();
    _countryCtrl.dispose();
    _postalCodeCtrl.dispose();
    _prefLocationsCtrl.dispose();
    _degreeCtrl.dispose();
    _branchCtrl.dispose();
    _universityCtrl.dispose();
    _gradYearCtrl.dispose();
    _gpaCtrl.dispose();
    _linkedinCtrl.dispose();
    _githubCtrl.dispose();
    _portfolioCtrl.dispose();
    _coverNoteCtrl.dispose();
    _yamlCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final client = requireClientFrom(ref);
      final p = await client.profile();
      _rawYaml = '${p['yaml'] ?? ''}';
      _yamlCtrl.text = _rawYaml;
      _profile = (p['profile'] as Map?)?.cast<String, dynamic>() ?? {};

      // Populate form controllers from profile dictionary
      final applicant = (_profile['applicant'] as Map?)?.cast<String, dynamic>() ?? {};
      final comp = (_profile['compensation'] as Map?)?.cast<String, dynamic>() ?? {};
      final exp = (_profile['experience'] as Map?)?.cast<String, dynamic>() ?? {};
      final search = (_profile['search'] as Map?)?.cast<String, dynamic>() ?? {};
      final loc = (applicant['location'] as Map?)?.cast<String, dynamic>() ?? {};
      final edu = (applicant['education'] as Map?)?.cast<String, dynamic>() ?? {};

      // Compensation
      _currentCtcCtrl.text = comp['current_ctc_inr'] != null ? '${comp['current_ctc_inr']}' : '';
      _expectedCtcCtrl.text = comp['expected_ctc_inr'] != null ? '${comp['expected_ctc_inr']}' : '';
      _minCtcCtrl.text = comp['min_ctc_inr'] != null ? '${comp['min_ctc_inr']}' : '';
      _noticeDaysCtrl.text = comp['notice_period_days'] != null ? '${comp['notice_period_days']}' : '30';
      _servingNotice = '${comp['serving_notice'] ?? 'No'}';
      _lastWorkingDayCtrl.text = '${comp['last_working_day'] ?? ''}';

      // Experience & Role
      _currentTitleCtrl.text = '${exp['current_title'] ?? applicant['headline'] ?? ''}';
      _currentCompanyCtrl.text = '${exp['current_company'] ?? ''}';
      _experienceYearsCtrl.text = '${applicant['experience_years'] ?? '1'}';
      _headlineCtrl.text = '${applicant['headline'] ?? ''}';
      _keywordsCtrl.text = ((search['keywords'] as List?) ?? const []).join(', ');
      _skillsCtrl.text = ((search['skills'] as List?) ?? const []).join(', ');

      // Contact & Location
      _nameCtrl.text = '${applicant['name'] ?? ''}';
      _emailCtrl.text = '${applicant['email'] ?? ''}';
      _phoneCtrl.text = '${applicant['phone'] ?? ''}';
      _cityCtrl.text = '${loc['city'] ?? applicant['current_location'] ?? ''}';
      _stateCtrl.text = '${loc['state'] ?? ''}';
      _countryCtrl.text = '${loc['country'] ?? 'India'}';
      _postalCodeCtrl.text = '${loc['postal_code'] ?? ''}';
      _willingToRelocate = '${applicant['willing_to_relocate'] ?? 'Yes'}';
      _prefLocationsCtrl.text = ((search['locations'] as List?) ?? const []).join(', ');

      // Education & Eligibility
      _degreeCtrl.text = '${edu['degree'] ?? "Bachelor's"}';
      _branchCtrl.text = '${edu['branch'] ?? ''}';
      _universityCtrl.text = '${edu['university'] ?? ''}';
      _gradYearCtrl.text = '${edu['graduation_year'] ?? ''}';
      _gpaCtrl.text = '${edu['gpa'] ?? ''}';
      _workAuth = '${applicant['work_authorization'] ?? 'Yes'}';
      _sponsorship = '${applicant['requires_sponsorship'] ?? 'No'}';
      _gender = '${applicant['gender'] ?? 'Prefer not to say'}';
      if (_gender.isEmpty) _gender = 'Prefer not to say';
      _disability = '${applicant['disability'] ?? 'Prefer not to say'}';
      _veteran = '${applicant['veteran'] ?? 'No'}';

      // Links & Cover
      _linkedinCtrl.text = '${applicant['linkedin'] ?? ''}';
      _githubCtrl.text = '${applicant['github'] ?? ''}';
      _portfolioCtrl.text = '${applicant['portfolio'] ?? ''}';
      _coverNoteCtrl.text = '${applicant['cover_note'] ?? ''}';

      // Load QA items
      _loadQa();
    } catch (e) {
      _msg = 'Error loading profile: $e';
      _isError = true;
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _loadQa() async {
    setState(() => _loadingQa = true);
    try {
      final client = requireClientFrom(ref);
      final qList = await client.formQuestions(all: true);
      if (mounted) setState(() => _qaList = qList);
    } catch (_) {
      // Ignore QA load errors gracefully
    } finally {
      if (mounted) setState(() => _loadingQa = false);
    }
  }

  Map<String, dynamic> _buildProfileJson() {
    int? parseInt(String text) {
      final s = text.trim().replaceAll(',', '').replaceAll('₹', '');
      return int.tryParse(s);
    }

    List<String> parseList(String text) {
      return text
          .split(',')
          .map((e) => e.trim())
          .where((e) => e.isNotEmpty)
          .toList();
    }

    // Merge onto copy of existing profile to preserve unedited sections
    final updated = Map<String, dynamic>.from(_profile);

    // Applicant
    final app = Map<String, dynamic>.from((updated['applicant'] as Map?) ?? {});
    app['name'] = _nameCtrl.text.trim();
    app['email'] = _emailCtrl.text.trim();
    app['phone'] = _phoneCtrl.text.trim();
    app['headline'] = _headlineCtrl.text.trim();
    app['experience_years'] = _experienceYearsCtrl.text.trim();
    app['current_location'] = _cityCtrl.text.trim();
    app['willing_to_relocate'] = _willingToRelocate;
    app['work_authorization'] = _workAuth;
    app['requires_sponsorship'] = _sponsorship;
    app['gender'] = _gender;
    app['disability'] = _disability;
    app['veteran'] = _veteran;
    app['linkedin'] = _linkedinCtrl.text.trim();
    app['github'] = _githubCtrl.text.trim();
    app['portfolio'] = _portfolioCtrl.text.trim();
    app['cover_note'] = _coverNoteCtrl.text.trim();

    // Location
    final loc = Map<String, dynamic>.from((app['location'] as Map?) ?? {});
    loc['city'] = _cityCtrl.text.trim();
    loc['state'] = _stateCtrl.text.trim();
    loc['country'] = _countryCtrl.text.trim();
    loc['postal_code'] = _postalCodeCtrl.text.trim();
    app['location'] = loc;

    // Education
    final edu = Map<String, dynamic>.from((app['education'] as Map?) ?? {});
    edu['degree'] = _degreeCtrl.text.trim();
    edu['branch'] = _branchCtrl.text.trim();
    edu['university'] = _universityCtrl.text.trim();
    edu['graduation_year'] = _gradYearCtrl.text.trim();
    edu['gpa'] = _gpaCtrl.text.trim();
    app['education'] = edu;
    updated['applicant'] = app;

    // Experience
    final exp = Map<String, dynamic>.from((updated['experience'] as Map?) ?? {});
    exp['current_company'] = _currentCompanyCtrl.text.trim();
    exp['current_title'] = _currentTitleCtrl.text.trim();
    exp['role_label'] = _headlineCtrl.text.trim().isNotEmpty ? _headlineCtrl.text.trim() : _currentTitleCtrl.text.trim();
    updated['experience'] = exp;

    // Compensation
    final comp = Map<String, dynamic>.from((updated['compensation'] as Map?) ?? {});
    comp['current_ctc_inr'] = parseInt(_currentCtcCtrl.text) ?? 0;
    comp['expected_ctc_inr'] = parseInt(_expectedCtcCtrl.text) ?? (parseInt(_currentCtcCtrl.text) ?? 0);
    comp['min_ctc_inr'] = parseInt(_minCtcCtrl.text) ?? (parseInt(_currentCtcCtrl.text) ?? 0);
    comp['notice_period_days'] = parseInt(_noticeDaysCtrl.text) ?? 30;
    comp['serving_notice'] = _servingNotice;
    comp['last_working_day'] = _lastWorkingDayCtrl.text.trim();
    updated['compensation'] = comp;

    // Search
    final search = Map<String, dynamic>.from((updated['search'] as Map?) ?? {});
    if (_keywordsCtrl.text.isNotEmpty) search['keywords'] = parseList(_keywordsCtrl.text);
    if (_skillsCtrl.text.isNotEmpty) search['skills'] = parseList(_skillsCtrl.text);
    if (_prefLocationsCtrl.text.isNotEmpty) search['locations'] = parseList(_prefLocationsCtrl.text);
    updated['search'] = search;

    return updated;
  }

  Future<void> _saveForm() async {
    setState(() {
      _saving = true;
      _msg = null;
      _isError = false;
    });

    try {
      final payload = _buildProfileJson();
      final client = requireClientFrom(ref);
      final res = await client.saveProfileJson(payload);
      final miss = (res['missing_profile_fields'] as List?) ?? const [];

      setState(() {
        _isError = false;
        _msg = miss.isEmpty
            ? 'Profile & Compensation saved! All ATS forms will auto-fill.'
            : 'Saved successfully. Missing fields: ${miss.join(', ')}';
      });

      ref.invalidate(statusProvider);
      // Reload raw YAML to keep both synchronized
      final fresh = await client.profile();
      if (mounted) {
        setState(() {
          _rawYaml = '${fresh['yaml'] ?? ''}';
          _yamlCtrl.text = _rawYaml;
        });
      }
    } catch (e) {
      setState(() {
        _isError = true;
        _msg = 'Save failed: $e';
      });
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _saveRawYaml() async {
    setState(() {
      _saving = true;
      _msg = null;
      _isError = false;
    });
    try {
      final client = requireClientFrom(ref);
      final res = await client.saveProfile(_yamlCtrl.text);
      final miss = (res['missing_profile_fields'] as List?) ?? const [];
      setState(() {
        _isError = false;
        _msg = miss.isEmpty ? 'YAML Profile saved!' : 'Saved with missing fields: ${miss.join(', ')}';
      });
      ref.invalidate(statusProvider);
      await _load();
    } catch (e) {
      setState(() {
        _isError = true;
        _msg = 'YAML error: $e';
      });
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _showAddCustomQaModal() {
    final qCtrl = TextEditingController();
    final aCtrl = TextEditingController();
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppTheme.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (ctx) => Padding(
        padding: EdgeInsets.only(
          left: 20,
          right: 20,
          top: 20,
          bottom: MediaQuery.of(ctx).viewInsets.bottom + 20,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.help_outline_rounded, color: AppTheme.primaryLight, size: 22),
                const SizedBox(width: 8),
                const Text('Pre-Answer ATS Question', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700, color: AppTheme.textPrimary)),
                const Spacer(),
                IconButton(icon: const Icon(Icons.close_rounded), onPressed: () => Navigator.pop(ctx)),
              ],
            ),
            const SizedBox(height: 12),
            const Text(
              'Add answers to common questions (e.g. "Notice period negotiable?", "Experience in Kubernetes?") so the AI never stops to ask you.',
              style: TextStyle(fontSize: 13, color: AppTheme.textSecondary),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: qCtrl,
              decoration: const InputDecoration(labelText: 'Question or Field Label', hintText: 'e.g. Are you willing to work in hybrid mode?'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: aCtrl,
              decoration: const InputDecoration(labelText: 'Your Saved Answer', hintText: 'e.g. Yes, absolutely.'),
            ),
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: () async {
                  if (qCtrl.text.trim().isEmpty || aCtrl.text.trim().isEmpty) return;
                  Navigator.pop(ctx);
                  try {
                    await requireClientFrom(ref).addCustomFormQuestion(qCtrl.text.trim(), aCtrl.text.trim());
                    _loadQa();
                    if (mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Screening answer saved!')));
                    }
                  } catch (e) {
                    if (mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed: $e')));
                    }
                  }
                },
                child: const Text('Save Answer'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Candidate Profile'),
        actions: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            child: FilledButton.icon(
              onPressed: _saving || _loading ? null : _saveForm,
              icon: _saving
                  ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Icon(Icons.check_rounded, size: 18),
              label: Text(_saving ? 'Saving...' : 'Save Profile'),
            ),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: AppTheme.primaryLight))
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                children: [
                  if (_msg != null)
                    Container(
                      margin: const EdgeInsets.only(bottom: 12),
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: _isError ? AppTheme.error.withValues(alpha: 0.15) : AppTheme.success.withValues(alpha: 0.15),
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(color: _isError ? AppTheme.error : AppTheme.success),
                      ),
                      child: Row(
                        children: [
                          Icon(_isError ? Icons.error_outline_rounded : Icons.check_circle_outline_rounded,
                              color: _isError ? AppTheme.error : AppTheme.success, size: 20),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              _msg!,
                              style: TextStyle(
                                color: _isError ? AppTheme.errorLight : AppTheme.successLight,
                                fontSize: 13,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),

                  // Header Explanation Card
                  Container(
                    margin: const EdgeInsets.only(bottom: 16),
                    padding: const EdgeInsets.all(16),
                    decoration: BoxDecoration(
                      color: AppTheme.card,
                      borderRadius: BorderRadius.circular(16),
                      border: Border.all(color: AppTheme.borderSubtle),
                    ),
                    child: const Row(
                      children: [
                        Icon(Icons.auto_awesome_rounded, color: AppTheme.secondaryLight, size: 24),
                        SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'Auto-Fill Master Profile',
                                style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: AppTheme.textPrimary),
                              ),
                              SizedBox(height: 2),
                              Text(
                                'Fill your salary, experience, and notice period here so the AI auto-fills career ATS forms without stopping.',
                                style: TextStyle(fontSize: 12.5, color: AppTheme.textSecondary),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),

                  // 1. Accordion: Compensation & Notice Period (PRIMARY FOCUS)
                  _buildAccordion(
                    icon: Icons.payments_outlined,
                    iconColor: AppTheme.secondaryLight,
                    title: 'Compensation & Notice Period',
                    subtitle: 'Current CTC, expected salary, and notice days for ATS',
                    initiallyExpanded: true,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: _buildTextField(
                              controller: _currentCtcCtrl,
                              label: 'Current CTC (INR)',
                              hint: 'e.g. 600000',
                              keyboardType: TextInputType.number,
                              prefixIcon: Icons.currency_rupee_rounded,
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: _buildTextField(
                              controller: _expectedCtcCtrl,
                              label: 'Expected CTC (INR)',
                              hint: 'e.g. 1200000',
                              keyboardType: TextInputType.number,
                              prefixIcon: Icons.trending_up_rounded,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: _buildTextField(
                              controller: _minCtcCtrl,
                              label: 'Minimum CTC (INR)',
                              hint: 'e.g. 900000',
                              keyboardType: TextInputType.number,
                              prefixIcon: Icons.lock_outline_rounded,
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: _buildTextField(
                              controller: _noticeDaysCtrl,
                              label: 'Notice Period (Days)',
                              hint: 'e.g. 15, 30, 60, 90',
                              keyboardType: TextInputType.number,
                              prefixIcon: Icons.calendar_today_rounded,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: _buildDropdown(
                              label: 'Currently Serving Notice?',
                              value: _servingNotice,
                              items: const ['No', 'Yes', 'Immediate'],
                              onChanged: (v) => setState(() => _servingNotice = v ?? 'No'),
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: _buildTextField(
                              controller: _lastWorkingDayCtrl,
                              label: 'Last Working Day (if any)',
                              hint: 'YYYY-MM-DD',
                              prefixIcon: Icons.event_available_rounded,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),

                  const SizedBox(height: 12),

                  // 2. Accordion: Role & Experience
                  _buildAccordion(
                    icon: Icons.work_outline_rounded,
                    iconColor: AppTheme.primaryLight,
                    title: 'Current Role & Experience',
                    subtitle: 'Current title, company, years, and target roles',
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: _buildTextField(
                              controller: _currentTitleCtrl,
                              label: 'Current Job Title',
                              hint: 'e.g. DevOps Engineer',
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: _buildTextField(
                              controller: _currentCompanyCtrl,
                              label: 'Current Company',
                              hint: 'e.g. Acme Tech',
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            flex: 1,
                            child: _buildTextField(
                              controller: _experienceYearsCtrl,
                              label: 'Total Exp (Years)',
                              hint: 'e.g. 2.5',
                              keyboardType: TextInputType.number,
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            flex: 2,
                            child: _buildTextField(
                              controller: _headlineCtrl,
                              label: 'Professional Headline',
                              hint: 'e.g. DevOps & Cloud Engineer',
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      _buildTextField(
                        controller: _keywordsCtrl,
                        label: 'Target Job Titles (comma separated)',
                        hint: 'DevOps Engineer, Cloud Engineer, SRE, Platform Engineer',
                        helperText: 'Titles searched and matched on LinkedIn, Naukri, & Career pages',
                      ),
                      const SizedBox(height: 12),
                      _buildTextField(
                        controller: _skillsCtrl,
                        label: 'Core Skills (comma separated)',
                        hint: 'AWS, Docker, Kubernetes, Terraform, CI/CD, Python, Linux',
                        helperText: 'Used to score job match relevance',
                      ),
                    ],
                  ),

                  const SizedBox(height: 12),

                  // 3. Accordion: Location & Contact
                  _buildAccordion(
                    icon: Icons.location_on_outlined,
                    iconColor: AppTheme.secondaryLight,
                    title: 'Location & Contact Details',
                    subtitle: 'Full name, email, phone, and relocation preferences',
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: _buildTextField(
                              controller: _nameCtrl,
                              label: 'Full Name',
                              hint: 'Candidate Name',
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: _buildTextField(
                              controller: _phoneCtrl,
                              label: 'Phone Number',
                              hint: '+91 9876543210',
                              keyboardType: TextInputType.phone,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      _buildTextField(
                        controller: _emailCtrl,
                        label: 'Email Address',
                        hint: 'you@example.com',
                        keyboardType: TextInputType.emailAddress,
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: _buildTextField(
                              controller: _cityCtrl,
                              label: 'City',
                              hint: 'Pune',
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: _buildTextField(
                              controller: _stateCtrl,
                              label: 'State',
                              hint: 'Maharashtra',
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: _buildTextField(
                              controller: _countryCtrl,
                              label: 'Country',
                              hint: 'India',
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: _buildTextField(
                              controller: _postalCodeCtrl,
                              label: 'Postal Code',
                              hint: '411001',
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: _buildDropdown(
                              label: 'Willing to Relocate?',
                              value: _willingToRelocate,
                              items: const ['Yes', 'No', 'Remote Only', 'Hybrid Only'],
                              onChanged: (v) => setState(() => _willingToRelocate = v ?? 'Yes'),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      _buildTextField(
                        controller: _prefLocationsCtrl,
                        label: 'Preferred Target Locations (comma separated)',
                        hint: 'Pune, Bangalore, Hyderabad, Remote',
                      ),
                    ],
                  ),

                  const SizedBox(height: 12),

                  // 4. Accordion: Education & Work Eligibility (ATS EEO)
                  _buildAccordion(
                    icon: Icons.school_outlined,
                    iconColor: AppTheme.info,
                    title: 'Education & Work Eligibility',
                    subtitle: 'Degree, authorization, sponsorship, and demographic defaults',
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: _buildTextField(
                              controller: _degreeCtrl,
                              label: 'Degree',
                              hint: 'B.Tech / B.E. / BCA / M.S.',
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: _buildTextField(
                              controller: _branchCtrl,
                              label: 'Branch / Major',
                              hint: 'Computer Science',
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      _buildTextField(
                        controller: _universityCtrl,
                        label: 'College / University',
                        hint: 'e.g. Pune University',
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: _buildTextField(
                              controller: _gradYearCtrl,
                              label: 'Graduation Year',
                              hint: '2024',
                              keyboardType: TextInputType.number,
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: _buildTextField(
                              controller: _gpaCtrl,
                              label: 'GPA / Percentage',
                              hint: '8.2 or 78%',
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: _buildDropdown(
                              label: 'Legally Authorized to Work?',
                              value: _workAuth,
                              items: const ['Yes', 'No'],
                              onChanged: (v) => setState(() => _workAuth = v ?? 'Yes'),
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: _buildDropdown(
                              label: 'Requires Sponsorship?',
                              value: _sponsorship,
                              items: const ['No', 'Yes'],
                              onChanged: (v) => setState(() => _sponsorship = v ?? 'No'),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: _buildDropdown(
                              label: 'Gender (EEO)',
                              value: _gender,
                              items: const ['Prefer not to say', 'Male', 'Female', 'Non-binary'],
                              onChanged: (v) => setState(() => _gender = v ?? 'Prefer not to say'),
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: _buildDropdown(
                              label: 'Disability Status',
                              value: _disability,
                              items: const ['Prefer not to say', 'No', 'Yes'],
                              onChanged: (v) => setState(() => _disability = v ?? 'Prefer not to say'),
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),

                  const SizedBox(height: 12),

                  // 5. Accordion: Links & Portfolio
                  _buildAccordion(
                    icon: Icons.link_rounded,
                    iconColor: AppTheme.secondaryLight,
                    title: 'Social & Portfolio Links',
                    subtitle: 'LinkedIn, GitHub, portfolio website, and cover note',
                    children: [
                      _buildTextField(
                        controller: _linkedinCtrl,
                        label: 'LinkedIn Profile URL',
                        hint: 'https://linkedin.com/in/username',
                        prefixIcon: Icons.business_center_rounded,
                      ),
                      const SizedBox(height: 12),
                      _buildTextField(
                        controller: _githubCtrl,
                        label: 'GitHub Profile URL',
                        hint: 'https://github.com/username',
                        prefixIcon: Icons.code_rounded,
                      ),
                      const SizedBox(height: 12),
                      _buildTextField(
                        controller: _portfolioCtrl,
                        label: 'Portfolio / Personal Website',
                        hint: 'https://yourwebsite.com',
                        prefixIcon: Icons.language_rounded,
                      ),
                      const SizedBox(height: 12),
                      _buildTextField(
                        controller: _coverNoteCtrl,
                        label: 'Default Cover Note / Note to Recruiter',
                        hint: 'Hello! I am excited to apply for this role. My resume is attached...',
                        maxLines: 3,
                      ),
                    ],
                  ),

                  const SizedBox(height: 12),

                  // 6. Accordion: Pre-Answered ATS Screening Questions (form_qa)
                  _buildAccordion(
                    icon: Icons.quiz_outlined,
                    iconColor: AppTheme.warningLight,
                    title: 'Pre-Answered ATS Questions',
                    subtitle: 'Questions encountered on previous forms that the AI remembers',
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text('${_qaList.length} Saved Questions', style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13, color: AppTheme.textSecondary)),
                          TextButton.icon(
                            icon: const Icon(Icons.add_rounded, size: 18),
                            label: const Text('Add Answer'),
                            onPressed: _showAddCustomQaModal,
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      if (_loadingQa)
                        const Center(child: Padding(padding: EdgeInsets.all(12), child: CircularProgressIndicator(strokeWidth: 2)))
                      else if (_qaList.isEmpty)
                        Container(
                          padding: const EdgeInsets.all(14),
                          decoration: BoxDecoration(
                            color: AppTheme.surface,
                            borderRadius: BorderRadius.circular(10),
                            border: Border.all(color: AppTheme.borderSubtle),
                          ),
                          child: const Text(
                            'No custom screening questions recorded yet. Tap "Add Answer" to pre-fill answers to tricky ATS screening questions.',
                            style: TextStyle(fontSize: 12.5, color: AppTheme.textMuted),
                          ),
                        )
                      else
                        for (final q in _qaList.take(15))
                          _buildQaItem(q),
                    ],
                  ),

                  const SizedBox(height: 12),

                  // 7. Accordion: Raw YAML View
                  _buildAccordion(
                    icon: Icons.code_rounded,
                    iconColor: AppTheme.textSecondary,
                    title: 'Advanced: Raw YAML File',
                    subtitle: 'View and manually edit profile.yaml directly on disk',
                    children: [
                      TextField(
                        controller: _yamlCtrl,
                        maxLines: 15,
                        style: const TextStyle(fontFamily: 'monospace', fontSize: 12, height: 1.4, color: AppTheme.textPrimary),
                        decoration: const InputDecoration(
                          hintText: '# YAML profile contents',
                        ),
                      ),
                      const SizedBox(height: 12),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.end,
                        children: [
                          OutlinedButton.icon(
                            icon: const Icon(Icons.refresh_rounded, size: 16),
                            label: const Text('Sync with Form'),
                            onPressed: () {
                              _buildProfileJson();
                              ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Form values synced.')));
                            },
                          ),
                          const SizedBox(width: 8),
                          FilledButton.icon(
                            icon: const Icon(Icons.save_rounded, size: 16),
                            label: const Text('Save Raw YAML'),
                            onPressed: _saving ? null : _saveRawYaml,
                          ),
                        ],
                      ),
                    ],
                  ),

                  const SizedBox(height: 32),
                ],
              ),
            ),
    );
  }

  Widget _buildAccordion({
    required IconData icon,
    required Color iconColor,
    required String title,
    required String subtitle,
    required List<Widget> children,
    bool initiallyExpanded = false,
  }) {
    return Container(
      decoration: BoxDecoration(
        color: AppTheme.card,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppTheme.borderSubtle, width: 1),
      ),
      clipBehavior: Clip.antiAlias,
      child: Theme(
        data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          initiallyExpanded: initiallyExpanded,
          tilePadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          childrenPadding: const EdgeInsets.only(left: 16, right: 16, bottom: 16),
          leading: Container(
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(
              color: iconColor.withValues(alpha: 0.15),
              borderRadius: BorderRadius.circular(10),
            ),
            child: Icon(icon, color: iconColor, size: 20),
          ),
          title: Text(
            title,
            style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15, color: AppTheme.textPrimary),
          ),
          subtitle: Text(
            subtitle,
            style: const TextStyle(fontSize: 12, color: AppTheme.textSecondary),
          ),
          children: children,
        ),
      ),
    );
  }

  Widget _buildTextField({
    required TextEditingController controller,
    required String label,
    String? hint,
    String? helperText,
    TextInputType keyboardType = TextInputType.text,
    IconData? prefixIcon,
    int maxLines = 1,
  }) {
    return TextField(
      controller: controller,
      keyboardType: keyboardType,
      maxLines: maxLines,
      style: const TextStyle(fontSize: 14, color: AppTheme.textPrimary),
      decoration: InputDecoration(
        labelText: label,
        hintText: hint,
        helperText: helperText,
        helperMaxLines: 2,
        prefixIcon: prefixIcon != null ? Icon(prefixIcon, size: 18, color: AppTheme.textMuted) : null,
      ),
    );
  }

  Widget _buildDropdown({
    required String label,
    required String value,
    required List<String> items,
    required ValueChanged<String?> onChanged,
  }) {
    final validValue = items.contains(value) ? value : items.first;
    return DropdownButtonFormField<String>(
      initialValue: validValue,
      decoration: InputDecoration(labelText: label),
      dropdownColor: AppTheme.surface,
      items: items.map((e) => DropdownMenuItem(value: e, child: Text(e, style: const TextStyle(fontSize: 13, color: AppTheme.textPrimary)))).toList(),
      onChanged: onChanged,
    );
  }

  Widget _buildQaItem(dynamic q) {
    final qMap = q as Map<String, dynamic>;
    final question = '${qMap['question'] ?? ''}';
    final answer = '${qMap['answer'] ?? ''}';
    final qid = qMap['id'] as int;

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppTheme.surface,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppTheme.borderSubtle),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  question,
                  style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13, color: AppTheme.textPrimary),
                ),
              ),
              IconButton(
                icon: const Icon(Icons.edit_rounded, size: 16, color: AppTheme.secondaryLight),
                onPressed: () {
                  final aCtrl = TextEditingController(text: answer);
                  showDialog(
                    context: context,
                    builder: (ctx) => AlertDialog(
                      backgroundColor: AppTheme.surface,
                      title: const Text('Update Answer', style: TextStyle(fontSize: 16, color: AppTheme.textPrimary)),
                      content: Column(
                        mainAxisSize: MainAxisSize.min,
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(question, style: const TextStyle(fontSize: 13, color: AppTheme.textSecondary)),
                          const SizedBox(height: 12),
                          TextField(controller: aCtrl, decoration: const InputDecoration(labelText: 'Answer')),
                        ],
                      ),
                      actions: [
                        TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
                        FilledButton(
                          onPressed: () async {
                            Navigator.pop(ctx);
                            try {
                              await requireClientFrom(ref).answerFormQuestion(qid, aCtrl.text.trim());
                              _loadQa();
                            } catch (_) {}
                          },
                          child: const Text('Save'),
                        ),
                      ],
                    ),
                  );
                },
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            answer.isNotEmpty ? 'Answer: $answer' : 'Waiting for answer (unresolved)',
            style: TextStyle(
              fontSize: 12,
              fontWeight: answer.isNotEmpty ? FontWeight.normal : FontWeight.w600,
              color: answer.isNotEmpty ? AppTheme.secondaryLight : AppTheme.warningLight,
            ),
          ),
        ],
      ),
    );
  }
}
