// Local, offline WinForms installer. Compile with build_installer.py.
// No game binaries are embedded. Compatibility never bypasses runtime guards.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

static class SetupProgram
{
    internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    internal static string ReadResource(string name)
    {
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(name))
        using (var reader = new StreamReader(stream, Encoding.UTF8)) return reader.ReadToEnd();
    }
    internal static string HashFile(string path)
    {
        using (var stream = File.OpenRead(path))
        using (var hash = SHA256.Create()) return BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
    }
    internal static string ClassifyHash(string hash)
    {
        var policy = Json.DeserializeObject(ReadResource("Compatibility.json")) as Dictionary<string, object>;
        foreach (string key in new[] { "supported", "unsupported" })
            foreach (object item in (object[])policy[key])
                if ((string)((Dictionary<string, object>)item)["sha256"] == hash) return key;
        return "unknown";
    }
    [STAThread]
    static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        try
        {
            if (args.Length == 2 && args[0] == "--self-test") return SelfTest(args[1]);
            if (args.Length == 4 && args[0] == "--test-install") return TestInstall(args[1], args[2], args[3]);
            using (var form = new SetupForm()) Application.Run(form);
            return 0;
        }
        catch (Exception ex)
        {
            if (args.Length > 0) { Console.Error.WriteLine(ex.ToString()); return 1; }
            MessageBox.Show(ex.Message, "PvZ MCP 安装器", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
    static int SelfTest(string output)
    {
        var metadata = Json.DeserializeObject(ReadResource("Metadata.json"));
        if (ClassifyHash("e1511201ec2ae9a7e30259f04bc2ec7272badb2574a2e9b405d0e916ae5c082c") != "supported") throw new Exception("Supported policy failed");
        if (ClassifyHash("678016e417cd55f1f19d7eabb6f2b7e6b87146be107db1179a61985078004f7a") != "unsupported") throw new Exception("Unsupported policy failed");
        if (ClassifyHash(new string('0', 64)) != "unknown") throw new Exception("Unknown policy failed");
        int entries;
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("Payload.zip"))
        using (var zip = new ZipArchive(stream, ZipArchiveMode.Read))
        {
            entries = zip.Entries.Count;
            if (zip.GetEntry("Install.ps1") == null || zip.GetEntry("release.json") == null) throw new Exception("Payload missing");
        }
        string folder = Path.GetDirectoryName(Path.GetFullPath(output)); Directory.CreateDirectory(folder);
        using (var form = new SetupForm())
        {
            form.RenderPreview(Path.Combine(folder, "installer-unsupported.png"));
        }
        File.WriteAllText(output, Json.Serialize(new { passed = true, metadata = metadata, payload_entries = entries,
            policy = "supported, unsupported and unknown classified", installs_unknown_game = false }), new UTF8Encoding(false));
        return 0;
    }
    static int TestInstall(string destination, string game, string report)
    {
        if (!Path.GetFullPath(destination).StartsWith(@"D:\Temp\Codex\", StringComparison.OrdinalIgnoreCase)) throw new Exception("Test installation must stay in its D-drive test workspace");
        int result = 1;
        using (var form = new SetupForm())
        {
            form.Headless = true;
            bool started = false;
            EventHandler run = null;
            run = async delegate {
                if (started) return;
                started = true;
                try
                {
                    await form.RunTest(destination, game == "none" ? "" : game);
                    result = form.LastInstallSucceeded ? 0 : 1;
                    Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(report)));
                    File.WriteAllText(report, Json.Serialize(new { passed = result == 0, message = form.LastInstallMessage,
                        configured = File.Exists(Path.Combine(destination, "data", "bridge-manifest.json")) }), new UTF8Encoding(false));
                }
                finally { Application.Idle -= run; Application.ExitThread(); }
            };
            Application.Idle += run;
            Application.Run(form);
        }
        return result;
    }
}

sealed class SetupForm : Form
{
    readonly TextBox installPath = new TextBox();
    readonly TextBox gamePath = new TextBox();
    readonly Label compatibility = new Label();
    readonly Label compatibilityDetail = new Label();
    readonly Label activity = new Label();
    readonly ProgressBar progress = new ProgressBar();
    readonly Button install = new Button();
    readonly Button close = new Button();
    readonly Button openFolder = new Button();
    readonly CheckBox toolsOnly = new CheckBox();
    readonly List<Control> fields = new List<Control>();
    readonly string version;
    bool busy;
    bool validationDisabled;
    int validationGeneration;
    internal bool Headless;
    internal bool LastInstallSucceeded;
    internal string LastInstallMessage { get { return activity.Text; } }

    protected override void SetVisibleCore(bool value)
    {
        if (Headless) { if (!IsHandleCreated) CreateHandle(); base.SetVisibleCore(false); }
        else base.SetVisibleCore(value);
    }
    internal async Task RunTest(string destination, string game)
    {
        installPath.Text = destination;
        gamePath.Text = game;
        toolsOnly.Checked = game.Length == 0;
        await Install();
    }
    internal void RenderPreview(string path)
    {
        validationDisabled = true; validationGeneration++;
        StartPosition = FormStartPosition.Manual; Location = new Point(-10000, -10000);
        ShowInTaskbar = false; Opacity = 0; Show(); Application.DoEvents();
        gamePath.Text = @"D:\Games\PvZ-1.0.0.1051\PlantsVsZombies.exe";
        SetCompatibility("unsupported", "1.0.0.1051");
        PerformLayout();
        using (var bitmap = new Bitmap(Width, Height))
        {
            DrawToBitmap(bitmap, new Rectangle(0, 0, Width, Height)); bitmap.Save(path);
        }
        Hide();
    }

    internal SetupForm()
    {
        var metadata = (Dictionary<string, object>)SetupProgram.Json.DeserializeObject(SetupProgram.ReadResource("Metadata.json"));
        version = (string)metadata["version"];
        Text = "PvZ MCP 安装器";
        Font = new Font("Microsoft YaHei UI", 9F);
        ClientSize = new Size(720, 590);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false; StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(248, 250, 252);
        var title = new Label { Text = "安装 PvZ MCP", Font = new Font(Font.FontFamily, 22F, FontStyle.Bold), AutoSize = true, Location = new Point(28, 20) };
        Controls.Add(title);
        AddLabel("版本 " + version + "  ·  离线安装  ·  不包含游戏", 30, 68, 650, 25, Color.DimGray);
        AddLabel("安装目录", 30, 112, 650, 24, Color.Black);
        ConfigurePath(installPath, 140);
        installPath.Text = Directory.Exists(@"D:\") ? @"D:\Programs\PvZ-MCP" : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "PvZ-MCP");
        var browseInstall = BrowseButton(140);
        browseInstall.Click += delegate { using (var d = new FolderBrowserDialog { Description = "选择工具安装目录", SelectedPath = installPath.Text }) if (d.ShowDialog(this) == DialogResult.OK) { installPath.Text = d.SelectedPath; LoadPreviousGame(); } };
        AddLabel("游戏程序（PlantsVsZombies.exe）", 30, 194, 650, 24, Color.Black);
        ConfigurePath(gamePath, 222);
        var browseGame = BrowseButton(222);
        browseGame.Click += delegate { using (var d = new OpenFileDialog { Title = "选择游戏 EXE", Filter = "游戏程序 (*.exe)|*.exe" }) if (d.ShowDialog(this) == DialogResult.OK) { toolsOnly.Checked = false; gamePath.Text = d.FileName; } };
        toolsOnly.Text = "暂不选择游戏，只安装工具（已有游戏配置会保留）";
        toolsOnly.SetBounds(30, 264, 650, 28); Controls.Add(toolsOnly); fields.Add(toolsOnly);
        compatibility.SetBounds(30, 310, 650, 30); compatibility.Font = new Font(Font.FontFamily, 12F, FontStyle.Bold); Controls.Add(compatibility);
        compatibilityDetail.SetBounds(30, 347, 650, 72); Controls.Add(compatibilityDetail);
        activity.SetBounds(30, 431, 650, 50); activity.ForeColor = Color.DimGray; Controls.Add(activity);
        progress.SetBounds(30, 491, 650, 12); Controls.Add(progress);
        install.Text = "安装 / 更新"; install.SetBounds(426, 528, 120, 36); Controls.Add(install);
        close.Text = "关闭"; close.SetBounds(560, 528, 120, 36); Controls.Add(close); close.Click += delegate { Close(); };
        openFolder.Text = "打开安装目录"; openFolder.SetBounds(30, 528, 150, 36); openFolder.Visible = false; Controls.Add(openFolder);
        openFolder.Click += delegate { Process.Start(new ProcessStartInfo(installPath.Text) { UseShellExecute = true }); };
        gamePath.TextChanged += async delegate { await ValidateGame(); };
        toolsOnly.CheckedChanged += async delegate { gamePath.Enabled = !toolsOnly.Checked; browseGame.Enabled = !toolsOnly.Checked; await ValidateGame(); };
        install.Click += async delegate { await Install(); };
        FormClosing += delegate(object sender, FormClosingEventArgs e) { if (busy) e.Cancel = true; };
        Shown += delegate { if (!validationDisabled) LoadPreviousGame(); };
        SetCompatibility("none", "");
        activity.Text = "仅安装工具。游戏、资源和存档不会随安装包分发。";
    }
    void AddLabel(string text, int x, int y, int width, int height, Color color)
    {
        Controls.Add(new Label { Text = text, Location = new Point(x, y), Size = new Size(width, height), ForeColor = color });
    }
    void ConfigurePath(TextBox field, int y)
    {
        field.SetBounds(30, y, 545, 30); Controls.Add(field); fields.Add(field);
    }
    Button BrowseButton(int y)
    {
        var button = new Button { Text = "浏览…" }; button.SetBounds(590, y - 1, 90, 32);
        Controls.Add(button); fields.Add(button); return button;
    }
    void LoadPreviousGame()
    {
        try
        {
            string file = Path.Combine(installPath.Text, "data", "bridge-manifest.json");
            if (File.Exists(file)) gamePath.Text = (string)((Dictionary<string, object>)SetupProgram.Json.DeserializeObject(File.ReadAllText(file)))["game"];
        }
        catch { /* Invalid previous config does not prevent tools-only installation. */ }
    }
    internal void SetCompatibility(string state, string fileVersion)
    {
        if (state == "supported")
        {
            compatibility.Text = "✓ 已验证可用  ·  " + fileVersion;
            compatibility.ForeColor = Color.FromArgb(24, 128, 70);
            compatibilityDetail.Text = "EXE 校验通过，安装后可通过 MCP 启动游戏并加载桥接。\n请在 AI 客户端导入安装目录中的 mcp-config.json。";
        }
        else if (state == "unsupported" || state == "unknown")
        {
            compatibility.Text = (state == "unsupported" ? "不支持此构建" : "尚未验证此构建") + (fileVersion.Length == 0 ? "" : "  ·  " + fileVersion);
            compatibility.ForeColor = Color.FromArgb(173, 79, 24);
            compatibilityDetail.Text = "可以继续安装工具，但不会为这个游戏加载桥接，游戏控制保持禁用。\n安装后可重新运行安装器，选择受支持的游戏。已有游戏配置会先备份。";
        }
        else
        {
            compatibility.Text = "只安装工具";
            compatibility.ForeColor = Color.FromArgb(50, 80, 120);
            compatibilityDetail.Text = "未选择游戏时也可以安装。选择受支持的 EXE 后才能控制游戏。\n当前仅支持已验证哈希的 1.2.0.1073，其他版本不会强行加载。";
        }
    }
    async Task ValidateGame()
    {
        if (IsDisposed || Disposing || validationDisabled) return;
        int generation = ++validationGeneration;
        if (toolsOnly.Checked || gamePath.Text.Trim().Length == 0) { SetCompatibility("none", ""); return; }
        string path = gamePath.Text.Trim();
        compatibility.Text = "正在校验游戏…";
        try
        {
            var result = await Task.Run(delegate {
                if (!File.Exists(path) || !path.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)) throw new IOException("请选择存在的游戏 EXE 文件。");
                return new[] { SetupProgram.ClassifyHash(SetupProgram.HashFile(path)), FileVersionInfo.GetVersionInfo(path).FileVersion ?? "未知版本" };
            });
            if (generation == validationGeneration && !IsDisposed && !Disposing) SetCompatibility(result[0], result[1]);
        }
        catch (Exception ex)
        {
            if (generation == validationGeneration && !IsDisposed && !Disposing) { compatibility.Text = "无法校验游戏"; compatibility.ForeColor = Color.Firebrick; compatibilityDetail.Text = ex.Message; }
        }
    }
    static string Quote(string value) { return "\"" + value.Replace("\"", "\\\"") + "\""; }
    static string ShellPath()
    {
        string[] folders = (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';');
        foreach (string folder in folders) { try { string p = Path.Combine(folder.Trim('"'), "pwsh.exe"); if (File.Exists(p)) return p; } catch { } }
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe");
    }
    async Task Install()
    {
        LastInstallSucceeded = false;
        string destination, selectedGame;
        try
        {
            destination = Path.GetFullPath(installPath.Text.Trim()).TrimEnd(Path.DirectorySeparatorChar);
            if (destination.Length < 4 || String.Equals(destination + Path.DirectorySeparatorChar, Path.GetPathRoot(destination), StringComparison.OrdinalIgnoreCase)) throw new IOException("请选择独立的工具安装目录，不能直接选择盘符根目录。");
            selectedGame = toolsOnly.Checked ? "" : gamePath.Text.Trim();
            if (selectedGame.Length > 0 && (!File.Exists(selectedGame) || !selectedGame.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))) throw new IOException("请选择存在的游戏 EXE，或勾选只安装工具。");
            if (selectedGame.Length > 0 && String.Equals(destination, Path.GetDirectoryName(Path.GetFullPath(selectedGame)), StringComparison.OrdinalIgnoreCase)) throw new IOException("请将工具安装在独立目录，不要选择游戏文件所在目录。");
        }
        catch (Exception ex) { MessageBox.Show(this, ex.Message, "检查安装位置", MessageBoxButtons.OK, MessageBoxIcon.Information); return; }
        busy = true; install.Enabled = false; close.Enabled = false; openFolder.Visible = false;
        foreach (Control c in fields) c.Enabled = false;
        string staging = null;
        try
        {
            string tempRoot = Directory.Exists(@"D:\") ? @"D:\Temp\Codex\PvZ-MCP-Setup" : Path.Combine(destination, ".setup");
            staging = Path.Combine(tempRoot, version + "-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(staging); activity.Text = "正在解包离线运行环境…";
            var report = new Progress<int>(delegate(int value) { progress.Value = Math.Max(0, Math.Min(100, value)); });
            await Task.Run(delegate { ExtractPayload(staging, report); });
            progress.Style = ProgressBarStyle.Marquee; activity.Text = "正在校验并安装。配置与备份会保留，请稍候…";
            string logDirectory = Path.Combine(destination, "data", "logs"); Directory.CreateDirectory(logDirectory);
            string logPath = Path.Combine(logDirectory, "setup-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".log");
            string arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File " + Quote(Path.Combine(staging, "Install.ps1")) + " -InstallDir " + Quote(destination) + " -NonInteractive";
            if (selectedGame.Length == 0) arguments += " -InstallToolsOnly";
            else arguments += " -GamePath " + Quote(selectedGame) + " -AllowUnsupportedGame";
            int code = await Task.Run(delegate {
                var start = new ProcessStartInfo(ShellPath(), arguments) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = staging, StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
                using (var process = Process.Start(start))
                {
                    Task<string> stdout = process.StandardOutput.ReadToEndAsync(); Task<string> stderr = process.StandardError.ReadToEndAsync();
                    process.WaitForExit(); Task.WaitAll(stdout, stderr);
                    File.WriteAllText(logPath, stdout.Result + "\n" + stderr.Result, new UTF8Encoding(false));
                    return process.ExitCode;
                }
            });
            if (code != 0) throw new IOException("安装失败。请查看日志：\n" + logPath);
            bool configured = File.Exists(Path.Combine(destination, "data", "bridge-manifest.json"));
            activity.Text = configured ? "安装完成。将 mcp-config.json 加入 AI 客户端，重新连接后从 MCP 启动游戏。" : "工具安装完成，尚未配置可用游戏。可重新运行安装器选择受支持的 EXE。";
            progress.Style = ProgressBarStyle.Blocks; progress.Value = 100;
            installPath.Text = destination; openFolder.Visible = true; install.Text = "已完成";
            LastInstallSucceeded = true;
        }
        catch (Exception ex)
        {
            activity.Text = ex.Message; progress.Style = ProgressBarStyle.Blocks; install.Enabled = true; install.Text = "重试安装";
            foreach (Control c in fields) c.Enabled = true;
        }
        finally
        {
            busy = false; close.Enabled = true;
            if (staging != null) PreserveStaging(staging);
        }
    }
    static void ExtractPayload(string staging, IProgress<int> report)
    {
        string root = Path.GetFullPath(staging).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("Payload.zip"))
        using (var zip = new ZipArchive(stream, ZipArchiveMode.Read))
        {
            int count = 0;
            foreach (var entry in zip.Entries)
            {
                string target = Path.GetFullPath(Path.Combine(root, entry.FullName.Replace('/', Path.DirectorySeparatorChar)));
                if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase)) throw new IOException("安装包包含无效路径。");
                if (entry.FullName.EndsWith("/")) Directory.CreateDirectory(target);
                else { Directory.CreateDirectory(Path.GetDirectoryName(target)); entry.ExtractToFile(target, false); }
                count++; if (count % 30 == 0) report.Report(count * 100 / zip.Entries.Count);
            }
        }
    }
    static void PreserveStaging(string staging)
    {
        try
        {
            string root = Path.GetFullPath(@"D:\Temp\Codex\PvZ-MCP-Setup") + Path.DirectorySeparatorChar;
            string source = Path.GetFullPath(staging);
            if (!source.StartsWith(root, StringComparison.OrdinalIgnoreCase) || !Directory.Exists(source)) return;
            string discard = @"D:\Temp\Codex\待删除\PvZ-MCP-Setup"; Directory.CreateDirectory(discard);
            string target = Path.GetFullPath(Path.Combine(discard, Path.GetFileName(source)));
            if (!target.StartsWith(Path.GetFullPath(discard) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) return;
            Directory.Move(source, target);
            File.WriteAllText(Path.Combine(discard, "清单.txt"), "这些目录是 EXE 安装器解出的临时离线包，可手动统一删除；不包含正式安装目录或游戏。", new UTF8Encoding(false));
        }
        catch { /* Retain the original staging folder if collection is unavailable. */ }
    }
}
