pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // iMin Printer SDK 2.0 (com.github.iminsoftware:IminPrinterLibrary), as in iMin's PrinterDemo.
        maven("https://jitpack.io") {
            content { includeGroup("com.github.iminsoftware") }
        }
    }
}
rootProject.name = "posnext-shell"
include(":app")
