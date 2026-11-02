using System;
using System.IO;
using System.Diagnostics;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Collections.Generic;
using System.Windows.Forms;

// A stable entry point. Resolve active.json each time, including after rollback.
class ControlLauncher {
    [STAThread]
    static int Main(string[] args) {
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            var active = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(
                File.ReadAllText(Path.Combine(root, "active.json")));
            string version = Convert.ToString(active["version"]);
            if (!Regex.IsMatch(version, @"^\d+\.\d+\.\d+(-[a-z0-9.]+)?$"))
                throw new Exception("Invalid active version");
            string release = Path.Combine(root, "versions", version);
            string python = Path.Combine(release, "runtime", "python", "pythonw.exe");
            string script = Path.Combine(release, "app", "control_panel.py");
            if (!File.Exists(python) || !File.Exists(script))
                throw new Exception("此版本没有控制面板，请更新到 0.3.5 或更高版本。");
            var start = new ProcessStartInfo(python, "\"" + script + "\"");
            start.WorkingDirectory = release;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.EnvironmentVariables["PVZ_DATA_DIR"] = Path.Combine(root, "data");
            start.EnvironmentVariables["PVZ_INSTALL_ROOT"] = root;
            start.EnvironmentVariables["PVZ_NODE"] = Path.Combine(release, "runtime", "node", "node.exe");
            start.EnvironmentVariables["PYTHONUTF8"] = "1";
            if (args.Length == 2 && args[0] == "--self-test") {
                File.WriteAllText(args[1], new JavaScriptSerializer().Serialize(new {
                    version = version, python = python, script = script,
                    data = start.EnvironmentVariables["PVZ_DATA_DIR"]
                }));
                return 0;
            }
            Process.Start(start);
            return 0;
        } catch (Exception ex) {
            MessageBox.Show(ex.Message, "PvZ MCP 控制台", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
