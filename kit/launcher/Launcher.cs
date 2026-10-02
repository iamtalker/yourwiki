// 유어위키.exe — 관리판을 띄우는 작은 실행기. 창 없이 파이썬(kit\tools\python)으로 kit\scripts\panel.py 를 실행한다.
// 도구가 아직 없는 처음 한 번은 설치 스크립트(kit\scripts\panel_launch.ps1)를 창과 함께 실행해 필요한 도구를 받는다.
// 빌드: kit\launcher\build.ps1 (윈도우에 기본으로 들어 있는 C# 컴파일러 csc 를 쓴다)
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("유어위키")]
[assembly: AssemblyProduct("YourWiki")]
[assembly: AssemblyDescription("유어위키 관리판 실행기")]
[assembly: AssemblyCompany("YourWiki")]
[assembly: AssemblyVersion("1.0.0.0")]

static class Program
{
    [STAThread]
    static int Main()
    {
        string top = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        string kit = Path.Combine(top, "kit");
        string py = Path.Combine(kit, "tools", "python", "python.exe");
        string panel = Path.Combine(kit, "scripts", "panel.py");
        string launch = Path.Combine(kit, "scripts", "panel_launch.ps1");

        if (!File.Exists(panel))
        {
            MessageBox.Show("kit 폴더를 찾지 못했습니다.\n유어위키.exe 는 kit 폴더와 같은 곳에 있어야 합니다.\n\n" + top,
                            "유어위키", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 1;
        }
        try
        {
            ProcessStartInfo psi;
            if (File.Exists(py))
            {
                // 평소: 창 없이 관리판만 띄운다(관리판이 브라우저를 열어 준다)
                psi = new ProcessStartInfo(py, "\"" + panel + "\"");
                psi.CreateNoWindow = true;
            }
            else
            {
                // 처음 한 번: 필요한 도구를 받는 동안 진행을 보여 주려고 창을 연다
                psi = new ProcessStartInfo("powershell.exe",
                    "-NoProfile -ExecutionPolicy Bypass -File \"" + launch + "\"");
            }
            psi.UseShellExecute = false;
            psi.WorkingDirectory = top;
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";
            Process.Start(psi);
            return 0;
        }
        catch (Exception e)
        {
            MessageBox.Show("관리판을 시작하지 못했습니다.\n\n" + e.Message, "유어위키", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
