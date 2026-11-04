import java.util.Properties

plugins {
    // AGP 9 compiles Kotlin itself (built-in Kotlin); no kotlin-android plugin.
    id("com.android.application")
}

// Release signing: keystore.properties (git-ignored) or POSNEXT_* env vars.
val keystoreProps = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
fun signingValue(key: String, env: String): String? =
    keystoreProps.getProperty(key) ?: System.getenv(env)

android {
    namespace = "id.posnext.shell"
    compileSdk = 36

    defaultConfig {
        applicationId = "id.posnext.shell"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "1.0.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    val storePath = signingValue("storeFile", "POSNEXT_KEYSTORE")
    signingConfigs {
        if (storePath != null) {
            create("release") {
                storeFile = file(storePath)
                storePassword = signingValue("storePassword", "POSNEXT_KEYSTORE_PASSWORD")
                keyAlias = signingValue("keyAlias", "POSNEXT_KEY_ALIAS")
                keyPassword = signingValue("keyPassword", "POSNEXT_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // Without a keystore the release APK is built unsigned.
            signingConfig = signingConfigs.findByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { buildConfig = true }
}

dependencies {
    implementation("com.github.iminsoftware:IminPrinterLibrary:V2.0.0.18")
    implementation("androidx.webkit:webkit:1.18.0")
    implementation("androidx.activity:activity-ktx:1.12.4")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20250517")
}
